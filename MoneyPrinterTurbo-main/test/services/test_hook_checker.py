"""Hook checker: playbook checks, AI hook score and better first lines."""

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services import hook_checker, llm, state as sm, version_checker, voice

WEBUI_MAIN = Path(__file__).resolve().parents[2] / "webui" / "Main.py"

GOOD = (
    "Fall into a black hole feet first, and you become spaghetti. "
    "Gravity at your feet pulls so much harder than at your head that your body gets stretched thinner "
    "and thinner, like noodles. Scientists literally call it spaghettification. But here's the creepy "
    "part. To a friend watching from far away, you never fall in. You slow down at the edge, fade to "
    "red, and freeze there forever. So which one is the real you? Would you jump in, yes or no?"
)


def _keys(report):
    return [c.key for c in report.checks]


def test_a_playbook_script_passes():
    report = hook_checker.local_checks(GOOD)
    assert _keys(report) == ["length_ideal", "hook_ok", "question_ok"]
    assert 25 <= report.seconds <= 40
    assert report.hook == "Fall into a black hole feet first, and you become spaghetti."


def test_weak_opening_long_hook_and_missing_question_are_flagged():
    script = (
        "Did you know that the universe is incredibly large and that it keeps on expanding faster "
        "and faster every single second? It is huge. Subscribe to Wonder Vault."
    )
    keys = _keys(hook_checker.local_checks(script))
    assert {"length_short", "hook_weak_opening", "hook_long", "question_missing", "outro"} <= set(keys)
    assert "hook_ok" not in keys


def test_telugu_greeting_and_channel_name_are_flagged():
    report = hook_checker.local_checks("నమస్కారం Wonder Vault కి స్వాగతం. ఈరోజు Black Hole గురించి చూద్దాం?")
    keys = _keys(report)
    assert "hook_weak_opening" in keys and "hook_channel_name" in keys
    assert report.checks[keys.index("hook_weak_opening")].values == {"phrase": "నమస్కారం"}


def test_faster_voice_shortens_the_estimate():
    slow = hook_checker.local_checks(GOOD, voice_rate=1.0).seconds
    fast = hook_checker.local_checks(GOOD, voice_rate=1.5).seconds
    assert fast < slow


def test_too_long_for_a_short():
    keys = _keys(hook_checker.local_checks(" ".join([GOOD] * 3)))
    assert keys[0] == "length_too_long"


def test_first_sentence_skips_separators_and_pause_tags():
    assert hook_checker.first_sentence("---\n[pause 1s] Sharks are older than trees! They are.") == (
        "Sharks are older than trees!"
    )


def test_replace_hook_keeps_the_rest_of_the_script():
    script = "Old hook here. Then the facts.\n\nLast line?"
    assert hook_checker.replace_hook(script, "New hook!") == "New hook! Then the facts.\n\nLast line?"


def test_ai_score_and_hooks():
    reply = json.dumps({"score": 12, "reason": "Strong  image.", "hooks": ["A", " ", "B", "C", "D"]})
    with patch.object(llm, "_generate_response", return_value=reply) as call:
        report = hook_checker.check(GOOD, "Black hole")
    assert report.score == 10 and report.reason == "Strong image."
    assert report.hooks == ["A", "B", "C"]
    assert "Current hook: Fall into a black hole" in call.call_args.kwargs["prompt"]


def test_ai_failure_keeps_the_playbook_checks():
    with patch.object(llm, "_generate_response", return_value="Error: quota exceeded"):
        report = hook_checker.check(GOOD)
    assert report.score is None and "quota" in report.ai_error
    assert _keys(report) == ["length_ideal", "hook_ok", "question_ok"]


def test_empty_script_is_rejected():
    with pytest.raises(ValueError):
        hook_checker.check("---\n")


def test_hook_check_on_the_page_and_use_a_suggestion(tmp_path):
    reply = json.dumps({"score": 6, "reason": "Too slow.", "hooks": ["Your body becomes spaghetti!"]})
    with ExitStack() as stack:
        stack.enter_context(patch.object(config, "app", {"llm_provider": "openai"}))
        stack.enter_context(patch.object(config, "ui", {"language": "en"}))
        stack.enter_context(patch.object(config, "try_save_config", return_value=True))
        stack.enter_context(patch.object(sm.state, "get_all_tasks", return_value=([], 0)))
        stack.enter_context(patch.object(voice, "get_all_azure_voices", return_value=["en-US-Jenny"]))
        stack.enter_context(patch.object(llm, "_generate_response", return_value=reply))
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
        app.text_area(key="video_script").input(GOOD).run()
        app.button(key="hook_check_btn").click().run()

        assert not app.exception
        texts = [m.value for m in app.markdown]
        assert any("in the 25–40 s sweet spot" in t for t in texts)
        assert any("Hook score: 6/10" in t for t in texts)
        app.button(key="hook_use_0").click().run()
        assert app.text_area(key="video_script").value.startswith(
            "Your body becomes spaghetti! Gravity at your feet"
        )
