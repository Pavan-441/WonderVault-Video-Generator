"""YouTube Kit tab rendered with real Streamlit widgets."""

import json
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app.config import config
from app.services import state as sm, version_checker, voice, youtube_kit

WEBUI_MAIN = Path(__file__).resolve().parents[2] / "webui" / "Main.py"

LLM_RESPONSE = json.dumps(
    {
        "titles": ["Mantis Shrimp: The Punch Faster Than a Bullet 🦐", "Option two 🌊", "Option three 😱"],
        "description_hook": "The mantis shrimp punches so fast the water boils.",
        "question": "Would you keep one as a pet — yes or no?",
        "pinned_comment": "Pet or no pet? 👇",
        "playlist": "Ocean Mysteries",
        "hashtags": ["#MantisShrimp", "#Ocean", "#Animals", "#ScienceFacts"],
        "tags": ["mantis shrimp", "mantis shrimp punch", "ocean facts"],
        "keywords": ["mantis shrimp", "mantis shrimp punch speed"],
    }
)


def _app(tmp_path):
    app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=60)
    app.session_state["ui_language"] = "en"
    return app


def _patches(tmp_path, ui_config):
    # AppTest cannot map the Audio Studio voice dropdown's display labels back to
    # voice IDs ("Female" is shown as "(Female)"), which breaks every rerun after
    # the first. A voice whose label equals its ID keeps that dropdown valid.
    return (
        patch.object(config, "app", {"llm_provider": "openai"}),
        patch.object(config, "ui", ui_config),
        patch.object(config, "try_save_config", return_value=True),
        patch.object(sm.state, "get_all_tasks", return_value=([], 0)),
        patch.object(voice, "get_all_azure_voices", return_value=["en-US-Jenny"]),
        patch.object(
            version_checker,
            "poll_available_update",
            return_value=version_checker.UpdateCheckSnapshot(complete=True),
        ),
        patch("app.utils.utils.task_dir", return_value=str(tmp_path)),
    )


def _code_blocks(app):
    return [block.value for block in app.code]


def test_youtube_kit_tab_generates_and_shows_every_section(tmp_path):
    ui_config = {"language": "en", "voice_mode": "none"}
    p = _patches(tmp_path, ui_config)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], patch.object(
        youtube_kit.llm, "_generate_response", return_value=LLM_RESPONSE
    ) as llm_call:
        app = _app(tmp_path)
        app.run()
        assert not app.exception
        assert any("YouTube Kit" in str(tab.label) for tab in app.tabs)
        assert app.text_input(key="yt_kit_channel").value == "@WonderVault-W"

        app.text_area(key="yt_kit_script").input(
            "The mantis shrimp punches faster than a bullet.\nWould you keep one as a pet?"
        )
        app.button(key="yt_kit_generate_btn").click().run()
        assert not app.exception

        llm_call.assert_called_once()
        blocks = _code_blocks(app)
        assert "Mantis Shrimp: The Punch Faster Than a Bullet 🦐" in blocks
        description = next(b for b in blocks if b.startswith("The mantis shrimp punches so fast"))
        assert "📖 The full fact:\nThe mantis shrimp punches faster than a bullet." in description
        assert "#MantisShrimp #Ocean #Animals #ScienceFacts #Shorts" in blocks
        assert "mantis shrimp, mantis shrimp punch, ocean facts" in blocks
        assert "Pet or no pet? 👇" in blocks
        assert ui_config["youtube_kit_channel"] == "@WonderVault-W"


def test_sync_copies_script_and_subject_from_video_generator(tmp_path):
    p = _patches(tmp_path, {"language": "en", "voice_mode": "none"})
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6]:
        app = _app(tmp_path)
        app.run()
        app.session_state["video_script"] = "Octopuses have three hearts."
        app.session_state["video_subject"] = "Octopus hearts"
        app.button(key="yt_kit_sync_btn").click().run()
        assert not app.exception
        assert app.text_area(key="yt_kit_script").value == "Octopuses have three hearts."
        assert app.text_input(key="yt_kit_subject").value == "Octopus hearts"


def test_missing_script_warns_and_llm_failure_shows_fallback_notice(tmp_path):
    p = _patches(tmp_path, {"language": "en", "voice_mode": "none"})
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], patch.object(
        youtube_kit.llm, "_generate_response", return_value="Error: quota exceeded"
    ):
        app = _app(tmp_path)
        app.run()
        app.button(key="yt_kit_generate_btn").click().run()
        assert any("Enter a script or a video subject first" in w.value for w in app.warning)

        app.text_input(key="yt_kit_subject").input("Why the sky is blue")
        app.button(key="yt_kit_generate_btn").click().run()
        assert not app.exception
        assert any("could not be reached" in w.value for w in app.warning)
        assert "Why the sky is blue" in _code_blocks(app)


def test_kids_audience_replaces_pinned_comment_with_made_for_kids_note(tmp_path):
    p = _patches(tmp_path, {"language": "en", "voice_mode": "none"})
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], patch.object(
        youtube_kit.llm, "_generate_response", return_value=LLM_RESPONSE
    ):
        app = _app(tmp_path)
        app.run()
        app.text_area(key="yt_kit_script").input("Shrimp can see colours we cannot.")
        app.selectbox(key="yt_kit_audience").select("kids")
        app.button(key="yt_kit_generate_btn").click().run()
        assert not app.exception
        assert any("Made for kids" in info.value for info in app.info)
        assert "Pet or no pet? 👇" not in _code_blocks(app)
