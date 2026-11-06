"""Other-language versions of a script and the voice saved for each language."""

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services import bilingual, llm, state as sm, version_checker, voice

WEBUI_MAIN = Path(__file__).resolve().parents[2] / "webui" / "Main.py"
TELUGU_SCRIPT = "Octopus కి మూడు హార్ట్స్ ఉంటాయి! మీకు తెలుసా?"
ENGLISH_SCRIPT = "Octopuses have three hearts! Did you know?"


def _reply(script, subject="Octopus hearts"):
    return json.dumps({"subject": subject, "script": script}, ensure_ascii=False)


def test_detect_and_other():
    assert bilingual.detect(TELUGU_SCRIPT) == bilingual.TELUGU
    assert bilingual.detect(ENGLISH_SCRIPT) == bilingual.ENGLISH
    assert bilingual.other(bilingual.TELUGU) == bilingual.ENGLISH
    assert bilingual.other(bilingual.ENGLISH) == bilingual.TELUGU


def test_translate_returns_script_and_subject():
    with patch.object(llm, "_generate_response", return_value="```json\n" + _reply(TELUGU_SCRIPT) + "\n```"):
        result = bilingual.translate(ENGLISH_SCRIPT, "Octopus", bilingual.TELUGU)
    assert result == {"subject": "Octopus hearts", "script": TELUGU_SCRIPT, "language": "telugu"}


def test_translate_retries_when_the_result_is_in_the_wrong_language():
    replies = [_reply(ENGLISH_SCRIPT), _reply(TELUGU_SCRIPT)]
    with patch.object(llm, "_generate_response", side_effect=replies) as call:
        result = bilingual.translate(ENGLISH_SCRIPT, "", bilingual.TELUGU)
    assert call.call_count == 2
    assert result["script"] == TELUGU_SCRIPT


def test_translate_raises_after_repeated_failures():
    with patch.object(llm, "_generate_response", return_value="Error: quota exceeded"):
        with pytest.raises(bilingual.TranslationError, match="quota"):
            bilingual.translate(ENGLISH_SCRIPT, "", bilingual.TELUGU)


def test_prompt_keeps_facts_tags_and_brand():
    prompt = bilingual.build_prompt("A [pause 1s] B", "Subj", bilingual.TELUGU)
    for rule in ("Keep every fact", "[pause 1s]", "Wonder Vault", "Telugu script", "English letters"):
        assert rule in prompt


def test_profiles_need_a_voice():
    ui = {"language_profiles": {"telugu": {"voice_name": "", "voice_rate": 1.1}}}
    assert bilingual.get_profile(ui, "telugu") is None
    profiles = bilingual.set_profile(
        ui,
        "telugu",
        {"tts_server": "gemini-tts", "voice_name": "gemini:Aoede", "voice_rate": "1.1", "extra": 1},
    )
    assert bilingual.get_profile({"language_profiles": profiles}, "telugu") == {
        "tts_server": "gemini-tts",
        "voice_name": "gemini:Aoede",
        "voice_rate": 1.1,
        "font_name": None,
        "video_language": None,
    }


@pytest.fixture
def run_webui(tmp_path):
    with ExitStack() as stack:

        def start(ui_config, llm_reply):
            stack.enter_context(patch.object(config, "app", {"llm_provider": "openai"}))
            stack.enter_context(patch.object(config, "ui", ui_config))
            stack.enter_context(patch.object(config, "try_save_config", return_value=True))
            stack.enter_context(patch.object(sm.state, "get_all_tasks", return_value=([], 0)))
            stack.enter_context(
                patch.object(
                    voice, "get_all_azure_voices", return_value=["en-US-Jenny", "en-US-Guy"]
                )
            )
            stack.enter_context(patch.object(llm, "_generate_response", return_value=llm_reply))
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


def _widget(elements, key):
    return next(e for e in elements if str(e.key).startswith(key))


def _base_ui(**extra):
    return {
        "language": "en",
        "tts_server": "azure-tts-v1",
        "voice_name": "en-US-Jenny",
        "voiceover_enabled": True,
        "voice_mode": "tts",
        "video_language": "en-US",
        **extra,
    }


def test_make_telugu_version_switches_script_language_and_voice_then_undo(run_webui):
    ui = _base_ui(
        language_profiles={
            "telugu": {
                "tts_server": "azure-tts-v1",
                "voice_name": "en-US-Guy",
                "voice_rate": 1.2,
                "font_name": "NotoSansTelugu-Bold.ttf",
                "video_language": "te-IN-Mix",
            }
        }
    )
    app = run_webui(ui, _reply(TELUGU_SCRIPT, "Octopus గుండెలు"))
    app.text_area(key="video_script").input(ENGLISH_SCRIPT).run()

    switch = app.button(key="bilingual_switch_btn")
    assert switch.label == "Make Telugu version"
    switch.click().run()

    assert not app.exception
    assert app.text_area(key="video_script").value == TELUGU_SCRIPT
    assert app.text_area(key="video_subject").value == "Octopus గుండెలు"
    assert _widget(app.selectbox, "script_language_select").value == "te-IN-Mix"
    assert _widget(app.selectbox, "speech_synthesis_select_azure-tts-v1").value == "en-US-Guy"
    assert _widget(app.selectbox, "voice_rate_select").value == 1.2
    assert _widget(app.selectbox, "font_name_select").value == "NotoSansTelugu-Bold.ttf"
    assert any("Telugu version ready, with your saved Telugu voice" in s.value for s in app.success)
    assert app.button(key="bilingual_switch_btn").label == "Make English version"

    app.button(key="bilingual_undo_btn").click().run()
    assert app.text_area(key="video_script").value == ENGLISH_SCRIPT
    assert _widget(app.selectbox, "script_language_select").value == "en-US"
    assert _widget(app.selectbox, "speech_synthesis_select_azure-tts-v1").value == "en-US-Jenny"


def test_without_a_saved_voice_the_script_changes_and_a_hint_is_shown(run_webui):
    app = run_webui(_base_ui(), _reply(TELUGU_SCRIPT))
    app.text_area(key="video_script").input(ENGLISH_SCRIPT).run()
    app.button(key="bilingual_switch_btn").click().run()

    assert app.text_area(key="video_script").value == TELUGU_SCRIPT
    assert _widget(app.selectbox, "script_language_select").value == "te-IN-Mix"
    assert _widget(app.selectbox, "speech_synthesis_select_azure-tts-v1").value == "en-US-Jenny"
    assert any("No Telugu voice is saved yet" in w.value for w in app.warning)


def test_translation_failure_keeps_the_script(run_webui):
    app = run_webui(_base_ui(), "Error: quota exceeded")
    app.text_area(key="video_script").input(ENGLISH_SCRIPT).run()
    app.button(key="bilingual_switch_btn").click().run()

    assert app.text_area(key="video_script").value == ENGLISH_SCRIPT
    assert any("Could not make the other-language version" in e.value for e in app.error)


def test_save_current_settings_as_a_language_voice(run_webui):
    ui = _base_ui()
    app = run_webui(ui, "")
    app.button(key="save_language_voice_english").click().run()

    assert bilingual.get_profile(ui, "english")["voice_name"] == "en-US-Jenny"
    assert bilingual.get_profile(ui, "english")["video_language"] == "en-US"
