"""Voice preview in the Audio Generator: hear and compare voices before the full audio."""

import ast
import re
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services import state as sm, version_checker, voice

WEBUI_MAIN = Path(__file__).resolve().parents[2] / "webui" / "Main.py"


def _load_preview_text_helper():
    """Compile only the pure helper and its constants; importing Main.py renders the page."""
    names = {
        "AUDIO_STUDIO_PREVIEW_MAX_CHARS",
        "AUDIO_STUDIO_PREVIEW_TARGET_CHARS",
        "_AUDIO_STUDIO_SEPARATOR_LINE",
        "_AUDIO_STUDIO_SENTENCE_END",
    }
    tree = ast.parse(WEBUI_MAIN.read_text(encoding="utf-8"))
    nodes = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)
        )
        or (isinstance(node, ast.FunctionDef) and node.name == "_audio_studio_preview_text")
    ]
    namespace = {"re": re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(WEBUI_MAIN), "exec"), namespace)
    return namespace["_audio_studio_preview_text"]


preview_text = _load_preview_text_helper()


def test_preview_stops_after_about_120_characters_of_whole_sentences():
    script = (
        "Your brain uses twenty percent of your energy. It weighs only two percent of your body. "
        "That is why thinking hard makes you tired. Scientists measured this in 1955."
    )
    assert preview_text(script) == (
        "Your brain uses twenty percent of your energy. It weighs only two percent of your body. "
        "That is why thinking hard makes you tired."
    )


def test_preview_skips_separator_lines_and_keeps_telugu():
    script = "---\nమీ మెదడు రోజుకు 20 శాతం శక్తిని వాడుతుంది.\n***\nఅది చాలా ఎక్కువ!"
    assert preview_text(script) == "మీ మెదడు రోజుకు 20 శాతం శక్తిని వాడుతుంది. అది చాలా ఎక్కువ!"


def test_preview_cuts_a_long_unpunctuated_script_at_a_word_boundary():
    script = " ".join(["word"] * 100)
    result = preview_text(script)
    assert len(result) <= 220
    assert result.endswith("word")


def test_empty_script_has_no_preview_text():
    assert preview_text(" \n---\n ") == ""


@pytest.fixture
def audio_studio(tmp_path):
    """Starts the WebUI with an in-memory config and a fake TTS that records its calls."""
    calls = []

    def fake_tts(text, voice_name, voice_rate, voice_file, voice_volume=1.0, **_):
        calls.append({"text": text, "voice_name": voice_name, "rate": voice_rate})
        Path(voice_file).write_bytes(b"ID3" + str(len(calls)).encode() * 64)
        return voice.SubMaker()

    with ExitStack() as stack:
        ui_config = {"language": "en", "tts_server": "azure-tts-v1", "voiceover_enabled": False}
        stack.enter_context(patch.object(config, "app", {"llm_provider": "openai"}))
        stack.enter_context(patch.object(config, "ui", ui_config))
        stack.enter_context(patch.object(config, "try_save_config", return_value=True))
        stack.enter_context(patch.object(sm.state, "get_all_tasks", return_value=([], 0)))
        stack.enter_context(
            patch.object(
                voice, "get_all_azure_voices", return_value=["en-US-Jenny", "en-US-Guy"]
            )
        )
        stack.enter_context(patch.object(voice, "tts", side_effect=fake_tts))
        stack.enter_context(patch.object(voice, "get_audio_duration", return_value=3.0))
        stack.enter_context(
            patch.object(
                version_checker,
                "poll_available_update",
                return_value=version_checker.UpdateCheckSnapshot(complete=True),
            )
        )
        stack.enter_context(patch("app.utils.utils.task_dir", return_value=str(tmp_path)))
        stack.enter_context(patch("app.utils.utils.storage_dir", return_value=str(tmp_path)))
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=60)
        app.session_state["ui_language"] = "en"
        app.run()
        assert not app.exception
        yield app, calls


def _preview_labels(app):
    return [c.value for c in app.caption if c.value.startswith(("▶️", "🕘"))]


def test_preview_reads_the_script_opening_and_compares_voices(audio_studio):
    app, calls = audio_studio
    app.text_area(key="audio_studio_script_area").input(
        "Octopuses have three hearts. Two pump blood to the gills. "
        "The third pumps it everywhere else, and it stops beating while they swim. "
        "That is why they prefer crawling."
    ).run()
    voice_select = app.selectbox(key="audio_studio_voice_select_azure-tts-v1")
    first, second = voice_select.value, next(
        v for v in voice_select.options if v != voice_select.value
    )

    app.button(key="audio_studio_preview_btn").click().run()
    assert not app.exception
    assert calls == [
        {
            "text": (
                "Octopuses have three hearts. Two pump blood to the gills. "
                "The third pumps it everywhere else, and it stops beating while they swim."
            ),
            "voice_name": first,
            "rate": 1.0,
        }
    ]
    assert _preview_labels(app) == [f"▶️ {first} · 1.0× · 100%"]

    app.selectbox(key="audio_studio_voice_select_azure-tts-v1").select(second).run()
    app.button(key="audio_studio_preview_btn").click().run()
    assert [c["voice_name"] for c in calls] == [first, second]
    assert _preview_labels(app) == [
        f"▶️ {second} · 1.0× · 100%",
        f"🕘 {first} · 1.0× · 100%",
    ]


def test_same_settings_replay_the_saved_preview_without_new_speech(audio_studio):
    app, calls = audio_studio
    app.text_area(key="audio_studio_script_area").input("Sharks are older than trees.").run()

    app.button(key="audio_studio_preview_btn").click().run()
    app.selectbox(key="audio_studio_voice_rate").select(1.2).run()
    app.button(key="audio_studio_preview_btn").click().run()
    app.selectbox(key="audio_studio_voice_rate").select(1.0).run()
    app.button(key="audio_studio_preview_btn").click().run()

    voice_name = calls[0]["voice_name"]
    assert [c["rate"] for c in calls] == [1.0, 1.2]
    assert _preview_labels(app) == [
        f"▶️ {voice_name} · 1.0× · 100%",
        f"🕘 {voice_name} · 1.2× · 100%",
    ]

    app.button(key="audio_studio_clear_previews_btn").click().run()
    assert _preview_labels(app) == []


def test_without_a_script_the_preview_uses_a_short_sample(audio_studio):
    app, calls = audio_studio
    assert any("Add a script to hear its first lines" in c.value for c in app.caption)

    app.button(key="audio_studio_preview_btn").click().run()
    assert not app.exception
    assert len(calls) == 1 and calls[0]["text"]
