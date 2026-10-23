"""Voiceover and background music are separate cards with their own toggles."""

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services import state as sm, version_checker, voice

WEBUI_MAIN = Path(__file__).resolve().parents[2] / "webui" / "Main.py"


@pytest.fixture
def run_webui(tmp_path):
    """Starts the WebUI with an in-memory config; patches stay active for reruns."""
    with ExitStack() as stack:

        def start(ui_config):
            stack.enter_context(patch.object(config, "app", {"llm_provider": "openai"}))
            stack.enter_context(patch.object(config, "ui", ui_config))
            stack.enter_context(patch.object(config, "try_save_config", return_value=True))
            stack.enter_context(patch.object(sm.state, "get_all_tasks", return_value=([], 0)))
            # A voice whose label equals its ID keeps the Audio Studio dropdown valid in AppTest.
            stack.enter_context(
                patch.object(voice, "get_all_azure_voices", return_value=["en-US-Jenny"])
            )
            stack.enter_context(
                patch.object(
                    version_checker,
                    "poll_available_update",
                    return_value=version_checker.UpdateCheckSnapshot(complete=True),
                )
            )
            stack.enter_context(patch("app.utils.utils.task_dir", return_value=str(tmp_path)))
            app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=60)
            app.session_state["ui_language"] = "en"
            app.run()
            assert not app.exception
            return app

        yield start


def _toggle(app, key):
    return next(t for t in app.toggle if t.key == key)


def _has_widget(elements, key):
    return any(str(getattr(e, "key", "")).startswith(key) for e in elements)


def test_music_without_voiceover(run_webui):
    ui_config = {"language": "en", "voiceover_enabled": False, "bgm_enabled": True}
    app = run_webui(ui_config)

    assert _toggle(app, "voiceover_enabled_toggle").value is False
    assert _toggle(app, "bgm_enabled_toggle").value is True
    assert not _has_widget(app.button_group, "voice_mode_control")
    assert _has_widget(app.selectbox, "bgm_type_select")
    assert any("No voiceover" in c.value for c in app.caption)
    assert ui_config["audio_enabled"] is True


def test_voiceover_without_music_hides_music_controls(run_webui):
    ui_config = {"language": "en", "voiceover_enabled": True, "bgm_enabled": False}
    app = run_webui(ui_config)

    assert _has_widget(app.button_group, "voice_mode_control")
    assert not _has_widget(app.selectbox, "bgm_type_select")
    assert any("No background music" in c.value for c in app.caption)
    assert ui_config["audio_enabled"] is True


def test_both_off_makes_a_silent_video(run_webui):
    ui_config = {"language": "en", "voiceover_enabled": True, "bgm_enabled": True}
    app = run_webui(ui_config)

    _toggle(app, "voiceover_enabled_toggle").set_value(False).run()
    _toggle(app, "bgm_enabled_toggle").set_value(False).run()

    assert not app.exception
    assert ui_config["voiceover_enabled"] is False
    assert ui_config["bgm_enabled"] is False
    assert ui_config["audio_enabled"] is False
    assert any("visual footage only" in i.value for i in app.info)


def test_turning_music_off_keeps_the_saved_source(run_webui):
    ui_config = {"language": "en", "bgm_enabled": True, "bgm_type": "preset"}
    app = run_webui(ui_config)

    _toggle(app, "bgm_enabled_toggle").set_value(False).run()
    assert ui_config["bgm_type"] == "preset"

    _toggle(app, "bgm_enabled_toggle").set_value(True).run()
    source = next(s for s in app.selectbox if str(s.key).startswith("bgm_type_select"))
    assert source.value == "preset"
    assert "" not in source.options


@pytest.mark.parametrize(
    ("old_ui", "voiceover_on", "music_on"),
    [
        ({"audio_enabled": False, "voice_mode": "tts", "bgm_type": "random"}, False, False),
        ({"voice_mode": "none", "bgm_type": "random"}, False, True),
        ({"voice_mode": "tts", "bgm_type": ""}, True, False),
        ({"tts_server": voice.NO_VOICE_NAME}, False, True),
        ({}, True, True),
    ],
)
def test_older_configs_map_onto_the_two_toggles(run_webui, old_ui, voiceover_on, music_on):
    app = run_webui({"language": "en", **old_ui})

    assert _toggle(app, "voiceover_enabled_toggle").value is voiceover_on
    assert _toggle(app, "bgm_enabled_toggle").value is music_on
