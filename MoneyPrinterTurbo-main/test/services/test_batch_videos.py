"""Batch videos from the script libraries."""

import json
import os
import threading
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from app.config import config
from app.models import const
from app.models.schema import VideoParams
from app.services import batch, bilingual, llm, script_library, state as sm, version_checker, voice

WEBUI_MAIN = Path(__file__).resolve().parents[2] / "webui" / "Main.py"

LIBRARY = """# Library

## Section 1

### Video #002 — Why Is the Sky Blue?

| WebUI field | Value |
| :--- | :--- |
| **Video Subject** | `Why is the sky blue?` |
| **Video Keywords** (in script order) | `blue sky, sunlight prism` |
| **Thumbnail Title** | `THE SKY LIES` |

#### 🎙️ Video Script (paste into 'Video Script (Optional)') — 20 words
```text
Sunlight scatters off air.
Blue scatters most.
```

#### 📤 Upload Kit
- **Title** (20 chars): `Why Is the Sky Blue? 🌈`
- **Playlist**: `Sky Facts`

---

> 📌 **Category:** Earth

### వీడియో #001 — Black Hole లో పడితే?

| **Video Subject** | `Black Hole లో పడితే?` |

#### 🎙️ వాయిస్‌ఓవర్ స్క్రిప్ట్
```text
Black Hole లో పడితే నూడుల్స్ అవుతారు!
```

## Section 2

### Video #003 — No script here

Just notes.
"""


def test_parse_library_reads_both_heading_styles():
    entries = script_library.parse_library(LIBRARY, "TEST_LIB")
    assert [e.number for e in entries] == [1, 2]
    telugu, sky = entries
    assert telugu.id == "TEST_LIB#001" and telugu.language == "telugu"
    assert telugu.subject == "Black Hole లో పడితే?" and telugu.upload_kit == ""
    assert sky.subject == "Why is the sky blue?"
    assert sky.keywords == "blue sky, sunlight prism"
    assert sky.thumbnail_title == "THE SKY LIES"
    assert sky.script == "Sunlight scatters off air.\nBlue scatters most."
    assert sky.upload_kit == "- **Title** (20 chars): `Why Is the Sky Blue? 🌈`\n- **Playlist**: `Sky Facts`"


def test_the_shipped_libraries_parse_completely():
    entries = script_library.load_all()
    assert len(entries) >= 250
    assert all(e.script and e.subject and e.upload_kit and e.keywords for e in entries)
    assert len({e.id for e in entries}) == len(entries)


def test_safe_folder_name():
    assert script_library.safe_folder_name('001 What? "Why"/How (Telugu)') == "001 What Why How (Telugu)"


def _entries():
    return script_library.parse_library(LIBRARY, "TEST_LIB")


def test_plan_jobs_for_each_language_choice():
    telugu, sky = _entries()
    assert [(j.entry.number, j.language) for j in batch.plan_jobs([telugu, sky], "as_written")] == [
        (1, "telugu"),
        (2, "english"),
    ]
    assert [j.language for j in batch.plan_jobs([sky], "other")] == ["telugu"]
    jobs = batch.plan_jobs([sky], "both")
    assert [(j.language, j.translated) for j in jobs] == [("english", False), ("telugu", True)]


TEMPLATE = VideoParams(
    video_subject="",
    video_language="en-US",
    voice_name="en-US-Jenny",
    video_source="local",
    custom_audio_file="old.mp3",
    material_weights=[1.0],
    video_count=3,
    subtitle_enabled=True,
)

PROFILES = {
    "language_profiles": {
        "telugu": {
            "tts_server": "gemini-tts",
            "voice_name": "gemini:Achernar",
            "voice_rate": 1.1,
            "font_name": "NotoSansTelugu-Bold.ttf",
            "video_language": "te-IN-Mix",
        }
    }
}


def test_build_params_uses_the_language_voice_and_stock_footage():
    telugu, _ = _entries()
    with patch.object(config, "ui", PROFILES):
        params = batch.build_params(
            TEMPLATE, batch.BatchJob(telugu, "telugu"), telugu.script, telugu.subject, "pixabay"
        )
    assert params.voice_name == "gemini:Achernar" and params.voice_rate == 1.1
    assert params.font_name == "NotoSansTelugu-Bold.ttf" and params.video_language == "te-IN-Mix"
    assert params.video_source == "pixabay" and params.video_count == 1
    assert params.custom_audio_file is None and params.material_weights is None
    assert params.video_terms is None and params.subtitle_enabled is True


def test_build_params_falls_back_to_the_template_voice_for_its_own_language():
    _, sky = _entries()
    with patch.object(config, "ui", {}):
        params = batch.build_params(TEMPLATE, batch.BatchJob(sky, "english"), sky.script, sky.subject, "pexels")
    assert params.voice_name == "en-US-Jenny"
    assert params.video_terms == "blue sky, sunlight prism"


def test_build_params_without_a_voice_for_the_language_fails_clearly():
    telugu, _ = _entries()
    with patch.object(config, "ui", {}), pytest.raises(batch.BatchError, match="no telugu voice"):
        batch.build_params(TEMPLATE, batch.BatchJob(telugu, "telugu"), "s", "x", "pexels")


@pytest.fixture
def sandbox(tmp_path):
    """Download and storage folders in tmp, and a fake pipeline that writes a video file."""
    calls = []

    def fake_start(task_id, params):
        calls.append(params)
        if "FAIL" in params.video_script:
            return {"state": const.TASK_STATE_FAILED, "error": "no stock footage found"}
        exported = tmp_path / "download" / f"{params.video_subject[:10]}.mp4"
        exported.parent.mkdir(exist_ok=True)
        exported.write_bytes(b"video")
        return {"videos": [str(exported)], "download_videos": [str(exported)]}

    with ExitStack() as stack:
        stack.enter_context(patch.object(config, "ui", PROFILES))
        stack.enter_context(patch.object(batch.utils, "download_dir", return_value=str(tmp_path / "download")))
        stack.enter_context(patch.object(batch, "status_file", return_value=str(tmp_path / "status.json")))
        stack.enter_context(patch.object(batch.tm, "start", side_effect=fake_start))
        stack.enter_context(patch.object(batch.thumbnail, "generate_hook_title", return_value="HOOK"))
        stack.enter_context(
            patch.object(
                llm,
                "_generate_response",
                return_value=json.dumps(
                    {"subject": "ఆకాశం", "script": "ఆకాశం నీలం ఎందుకు?", "titles": ["t 🌈"]},
                    ensure_ascii=False,
                ),
            )
        )
        yield tmp_path, calls


def _run(runner, jobs):
    assert runner.start(jobs, TEMPLATE, "pexels")
    runner._thread.join(timeout=30)
    return runner.snapshot()


def test_batch_makes_each_video_with_its_kit_and_thumbnail_prompt(sandbox):
    tmp_path, calls = sandbox
    telugu, sky = _entries()
    rows = _run(batch.BatchRunner(), batch.plan_jobs([sky], "both"))

    assert [row["state"] for row in rows] == ["done", "done"]
    english_folder = Path(rows[0]["folder"])
    assert english_folder.name == "002 Why Is the Sky Blue (English)"
    assert sorted(os.listdir(english_folder)) == [
        "Why is the.mp4",
        "script.txt",
        "thumbnail_prompt.txt",
        "upload_kit.md",
    ]
    # The library's own kit is used as written; the thumbnail uses the library's title.
    assert "Sky Facts" in (english_folder / "upload_kit.md").read_text(encoding="utf-8")
    assert "THE SKY LIES" in (english_folder / "thumbnail_prompt.txt").read_text(encoding="utf-8")

    # The Telugu version is translated, voiced with the Telugu profile and gets a new kit.
    telugu_folder = Path(rows[1]["folder"])
    assert telugu_folder.name == "002 Why Is the Sky Blue (Telugu)"
    assert calls[1].video_script == "ఆకాశం నీలం ఎందుకు?"
    assert calls[1].voice_name == "gemini:Achernar"
    assert "HOOK" in (telugu_folder / "thumbnail_prompt.txt").read_text(encoding="utf-8")

    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["TEST_LIB#002:english"]["state"] == "done"
    assert status["TEST_LIB#002:telugu"]["state"] == "done"


def test_a_failed_video_is_reported_and_the_batch_continues(sandbox):
    tmp_path, _ = sandbox
    telugu, sky = _entries()
    broken = script_library.LibraryScript("TEST_LIB", 9, "Broken", "Broken", "FAIL please")
    rows = _run(batch.BatchRunner(), batch.plan_jobs([broken, sky], "as_written"))

    assert [row["state"] for row in rows] == ["failed", "done"]
    assert rows[0]["error"] == "no stock footage found"
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["TEST_LIB#009:english"]["state"] == "failed"


def test_stop_finishes_the_current_video_and_skips_the_rest(sandbox):
    _, calls = sandbox
    _, sky = _entries()
    runner = batch.BatchRunner()
    original = batch.tm.start.side_effect

    def slow_start(task_id, params):
        runner.stop()
        return original(task_id, params)

    batch.tm.start.side_effect = slow_start
    rows = _run(runner, batch.plan_jobs([sky, sky, sky], "as_written"))
    assert [row["state"] for row in rows] == ["done", "skipped", "skipped"]
    assert len(calls) == 1


def test_only_one_batch_runs_at_a_time(sandbox):
    _, sky = _entries()
    runner = batch.BatchRunner()
    release = threading.Event()
    original = batch.tm.start.side_effect
    batch.tm.start.side_effect = lambda **kw: (release.wait(10), original(**kw))[1]
    assert runner.start(batch.plan_jobs([sky], "as_written"), TEMPLATE, "pexels")
    assert not runner.start(batch.plan_jobs([sky], "as_written"), TEMPLATE, "pexels")
    release.set()
    runner._thread.join(timeout=30)


def test_marking_done_and_not_done(tmp_path):
    with patch.object(batch, "status_file", return_value=str(tmp_path / "s.json")):
        batch.set_done("LIB#001", "telugu", True)
        assert batch.load_status()["LIB#001:telugu"]["state"] == "done"
        batch.set_done("LIB#001", "telugu", False)
        assert batch.load_status() == {}


def test_batch_tab_lists_library_scripts(tmp_path):
    with ExitStack() as stack:
        stack.enter_context(patch.object(config, "app", {"llm_provider": "openai"}))
        stack.enter_context(patch.object(config, "ui", {"language": "en"}))
        stack.enter_context(patch.object(config, "try_save_config", return_value=True))
        stack.enter_context(patch.object(sm.state, "get_all_tasks", return_value=([], 0)))
        stack.enter_context(patch.object(voice, "get_all_azure_voices", return_value=["en-US-Jenny"]))
        stack.enter_context(patch.object(batch, "status_file", return_value=str(tmp_path / "s.json")))
        stack.enter_context(
            patch.object(
                version_checker,
                "poll_available_update",
                return_value=version_checker.UpdateCheckSnapshot(complete=True),
            )
        )
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=60)
        app.session_state["ui_language"] = "en"
        app.run()
        assert not app.exception
        assert any("Batch Videos" in str(tab.label) for tab in app.tabs)
        start = app.button(key="batch_start_btn")
        assert start.label == "Start 0 videos" and start.disabled
