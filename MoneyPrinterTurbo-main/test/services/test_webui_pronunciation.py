"""Pronunciation Fixes editor on the Audio Generator page."""

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services import pronunciation, state as sm, version_checker, voice
from app.services.pronunciation import Rule

WEBUI_MAIN = Path(__file__).resolve().parents[2] / "webui" / "Main.py"


@pytest.fixture
def run_webui(tmp_path):
    """Starts the WebUI with an in-memory config and a temporary rules file."""
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(pronunciation, "rules_file", return_value=str(tmp_path / "p.json"))
        )
        pronunciation._cache.update(mtime=None, rules=[])

        def start(ui_config):
            stack.enter_context(patch.object(config, "app", {"llm_provider": "openai"}))
            stack.enter_context(patch.object(config, "ui", ui_config))
            stack.enter_context(patch.object(config, "try_save_config", return_value=True))
            stack.enter_context(patch.object(sm.state, "get_all_tasks", return_value=([], 0)))
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
    pronunciation._cache.update(mtime=None, rules=[])


def test_editor_shows_saved_fixes_and_previews_a_sentence(run_webui):
    pronunciation.save_rules([Rule("DNA", "డీ ఎన్ ఏ", "telugu")])
    app = run_webui({"language": "en", "tts_server": "azure-tts-v1"})

    assert any(e.label == "🗣️ Pronunciation Fixes (1)" for e in app.expander)
    app.text_input(key="pronunciation_try_input").input("మన DNA చూడండి").run()
    assert any("The voice will read: మన డీ ఎన్ ఏ చూడండి" in c.value for c in app.caption)


def test_video_generator_mentions_active_fixes(run_webui):
    pronunciation.save_rules([Rule("DNA", "డీ ఎన్ ఏ", "telugu"), Rule("NASA", "naasaa", "all")])
    app = run_webui(
        {"language": "en", "tts_server": "azure-tts-v1", "voiceover_enabled": True, "voice_mode": "tts"}
    )
    assert any("2 pronunciation fixes are on" in c.value for c in app.caption)


def test_no_fixes_no_notice(run_webui):
    app = run_webui(
        {"language": "en", "tts_server": "azure-tts-v1", "voiceover_enabled": True, "voice_mode": "tts"}
    )
    assert any(e.label == "🗣️ Pronunciation Fixes (0)" for e in app.expander)
    assert not any("pronunciation fixes are on" in c.value for c in app.caption)
