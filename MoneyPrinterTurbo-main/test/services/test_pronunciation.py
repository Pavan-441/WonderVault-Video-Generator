"""Pronunciation fixes change what the voice reads, never what the subtitles show."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import pronunciation, voice
from app.services.pronunciation import Rule

DNA = Rule("DNA", "డీ ఎన్ ఏ", "telugu")
NASA = Rule("NASA", "naasaa", "all")


@pytest.fixture
def rules_path(tmp_path):
    path = tmp_path / "pronunciation.json"
    with patch.object(pronunciation, "rules_file", return_value=str(path)):
        pronunciation._cache.update(mtime=None, rules=[])
        yield path
    pronunciation._cache.update(mtime=None, rules=[])


def test_telugu_rules_apply_only_to_telugu_scripts():
    rules = [DNA, NASA]
    assert pronunciation.apply("మన DNA లో NASA", rules=rules) == "మన డీ ఎన్ ఏ లో naasaa"
    assert pronunciation.apply("Our DNA and NASA", rules=rules) == "Our DNA and naasaa"


def test_whole_words_only_and_case_insensitive_for_english():
    rules = [Rule("cell", "sel", "all")]
    assert pronunciation.apply("A Cell, many cells.", rules=rules) == "A sel, many cells."


def test_telugu_word_does_not_match_inside_a_longer_word():
    rules = [Rule("కాలం", "kaalam", "telugu")]
    assert pronunciation.apply("కాలం కాలంలో", rules=rules) == "kaalam కాలంలో"


def test_longest_rule_wins_and_pause_tags_are_untouched():
    rules = [Rule("hole", "hol", "all"), Rule("black hole", "blak hol", "all"), Rule("pause", "x", "all")]
    text = "A black hole [pause 1s] and a hole."
    assert pronunciation.apply(text, rules=rules) == "A blak hol [pause 1s] and a hol."


def test_clean_rules_drops_empty_rows_and_keeps_last_duplicate():
    rules = pronunciation.clean_rules(
        [
            {"word": " DNA ", "say": "first", "scope": "telugu"},
            {"word": "", "say": "x"},
            {"word": "same", "say": "same"},
            {"word": "dna", "say": "second", "scope": "bogus"},
        ]
    )
    assert rules == [Rule("dna", "second", "telugu")]


def test_save_and_load_round_trip(rules_path):
    pronunciation.save_rules([DNA, {"word": "", "say": ""}])
    assert json.loads(Path(rules_path).read_text(encoding="utf-8")) == {
        "rules": [{"word": "DNA", "say": "డీ ఎన్ ఏ", "scope": "telugu"}]
    }
    assert pronunciation.load_rules() == [DNA]


def test_missing_or_broken_file_means_no_rules(rules_path):
    assert pronunciation.load_rules() == []
    Path(rules_path).write_text("{not json", encoding="utf-8")
    assert pronunciation.load_rules() == []


def test_tts_sends_fixed_text_to_the_voice_service(rules_path):
    pronunciation.save_rules([DNA])
    with patch.object(voice, "_single_tts", return_value="ok") as single:
        voice.tts("మన DNA చూడండి.", "te-IN-MohanNeural-Male", 1.0, "out.mp3")
    assert single.call_args.kwargs["text"] == "మన డీ ఎన్ ఏ చూడండి."


def test_no_voice_mode_keeps_the_original_text(rules_path):
    pronunciation.save_rules([DNA])
    with patch.object(voice, "_single_tts", return_value="ok") as single:
        voice.tts("మన DNA చూడండి.", voice.NO_VOICE_NAME, 1.0, "out.mp3")
    assert single.call_args.kwargs["text"] == "మన DNA చూడండి."


def test_subtitles_match_spoken_fix_back_to_the_script_line(rules_path):
    pronunciation.save_rules([DNA])
    lines = ["మన DNA చాలా పొడవు", "నిజమే"]
    assert voice._match_script_line(lines, "మన డీ ఎన్ ఏ చాలా పొడవు", 0) == "మన DNA చాలా పొడవు"


def test_subtitles_match_when_a_fix_and_a_year_are_on_one_line(rules_path):
    pronunciation.save_rules([Rule("NASA", "naasaa", "all")])
    spoken = voice.normalize_years_for_tts("naasaa started in 1958", "en-US")
    assert voice._match_script_line(["NASA started in 1958"], spoken, 0) == "NASA started in 1958"
