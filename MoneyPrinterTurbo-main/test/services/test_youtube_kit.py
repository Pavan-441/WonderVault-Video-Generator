"""YouTube Kit: metadata structure, YouTube limits, Telugu handling and LLM fallback."""

import json
from unittest.mock import patch

import pytest

from app.services import youtube_kit as yk

ENGLISH_SCRIPT = (
    "Fall into a black hole feet first, and you become spaghetti.\n"
    "---\n"
    "Scientists literally call it spaghettification. [pause 1s]\n"
    "Would you jump in, yes or no?"
)
TELUGU_SCRIPT = "Black Hole లోకి కాళ్ళతో పడితే, మీ బాడీ నూడుల్స్ లా సాగిపోతుంది! మీరైతే దూకుతారా?"


def _response(**overrides):
    data = {
        "titles": [
            "What Happens If You Fall Into a Black Hole? 🕳️",
            "Black Holes Turn You Into Spaghetti 🍝",
            "Spaghettification: The Scariest Way to Die 😱",
        ],
        "description_hook": "Falling into a black hole stretches you like spaghetti.",
        "english_summary": "",
        "romanized_search": "",
        "question": "Would you jump into a black hole — yes or no?",
        "pinned_comment": "Would you jump in? 👇",
        "playlist": "Space Mysteries",
        "hashtags": ["#BlackHole", "#Space", "#Astronomy", "#ScienceFacts"],
        "tags": ["what happens if you fall into a black hole", "black hole", "spaghettification"],
        "keywords": ["black hole", "spaghettification", "event horizon"],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def _generate(response, **kwargs):
    with patch.object(yk.llm, "_generate_response", return_value=response) as llm_call:
        kit = yk.generate_kit(kwargs.pop("script", ENGLISH_SCRIPT), **kwargs)
    return kit, llm_call


def test_kit_follows_upload_kit_structure():
    kit, _ = _generate(_response())

    assert not kit.used_fallback
    assert kit.language == "english"
    assert len(kit.titles) == 3
    assert kit.hashtags == ["#BlackHole", "#Space", "#Astronomy", "#ScienceFacts", "#Shorts"]
    blocks = kit.description.split("\n\n")
    assert blocks[0] == "Falling into a black hole stretches you like spaghetti."
    assert blocks[1].startswith("📖 The full fact:\nFall into a black hole feet first")
    assert "💬 Would you jump into a black hole — yes or no?" in kit.description
    assert '📺 More like this: the "Space Mysteries" playlist on @WonderVault-W.' in kit.description
    assert "🔔 Follow @WonderVault-W for a new mind-blowing fact every day." in kit.description
    assert kit.description.endswith("#BlackHole #Space #Astronomy #ScienceFacts #Shorts")
    assert kit.pinned_comment == "Would you jump in? 👇"


def test_narration_in_description_drops_pause_tags_and_separators():
    kit, _ = _generate(_response())

    assert "[pause" not in kit.description
    assert "\n---\n" not in kit.description
    assert "Scientists literally call it spaghettification." in kit.description


def test_titles_and_text_respect_youtube_limits():
    long_title = "Black hole " * 20 + "#Shorts #Space"
    kit, _ = _generate(
        _response(
            titles=[long_title, "Plain <b>title</b> 🕳️"],
            description_hook="Hook with <angle> brackets",
            tags=[f"tag number {i} about black holes" for i in range(40)],
        )
    )

    assert all(len(title) <= yk.TITLE_MAX_CHARS for title in kit.titles)
    assert all("#" not in title for title in kit.titles)
    assert "Plain btitle/b 🕳️" in kit.titles
    assert "<" not in kit.description and ">" not in kit.description
    assert yk.tags_character_count(kit.tags) <= yk.TAGS_MAX_CHARS
    assert len(kit.description) <= yk.DESCRIPTION_MAX_CHARS
    assert kit.warnings  # the first title is over the 60-character feed target


def test_long_script_is_shortened_to_fit_the_description_limit():
    script = "A fact about stars. " * 400
    kit, _ = _generate(_response(), script=script)

    assert len(kit.description) <= yk.DESCRIPTION_MAX_CHARS
    assert kit.description.endswith("#Shorts")


def test_tag_count_includes_quotes_and_commas():
    assert yk.tags_character_count(["black hole", "space"]) == len('"black hole",space')
    tags = yk.normalize_tags(["Black Hole", "black hole", "#Space", "  "])
    assert tags == ["black hole", "space"]


def test_long_format_omits_shorts_hashtag_and_uses_video_heading():
    kit, llm_call = _generate(_response(), video_format="long")

    assert "#Shorts" not in kit.hashtags
    assert len(kit.hashtags) == 4
    assert "📖 In this video:" in kit.description
    assert "regular long-form YouTube video" in llm_call.call_args.kwargs["prompt"]


def test_telugu_kit_adds_english_and_romanized_search_lines():
    response = _response(
        titles=["Black Hole లో పడితే ఏమవుతుంది? 🕳️ | Black Hole Explained in Telugu"],
        description_hook="Black Hole లో పడితే బాడీ నూడుల్స్ లా సాగిపోతుంది.",
        english_summary="What happens if you fall into a black hole — explained in Telugu.",
        romanized_search="black hole lo padithe em avuthundi",
        question="మీరైతే దూకుతారా? Yes or No?",
        playlist="Space Facts Telugu",
        hashtags=["#BlackHole", "#TeluguFacts", "#SpaceFacts", "#Telugu"],
    )
    kit, llm_call = _generate(response, script=TELUGU_SCRIPT)

    assert kit.language == "telugu"
    head = kit.description.split("\n\n")[0].split("\n")
    assert head == [
        "Black Hole లో పడితే బాడీ నూడుల్స్ లా సాగిపోతుంది.",
        "What happens if you fall into a black hole — explained in Telugu.",
        "🔎 black hole lo padithe em avuthundi",
    ]
    assert "📖 ఈ Short లో:" in kit.description
    assert '📺 ఇలాంటి మరిన్ని వీడియోలు: "Space Facts Telugu" Playlist చూడండి.' in kit.description
    assert "Romanized" in llm_call.call_args.kwargs["prompt"] or "English letters" in llm_call.call_args.kwargs["prompt"]


def test_telugu_hashtags_keep_vowel_signs():
    assert yk.normalize_hashtags(["తెలుగు ఫ్యాక్ట్స్"], "long") == ["#తెలుగుఫ్యాక్ట్స్"]


def test_language_override_and_auto_detection():
    assert yk.resolve_language("auto", TELUGU_SCRIPT) == "telugu"
    assert yk.resolve_language("auto", ENGLISH_SCRIPT) == "english"
    assert yk.resolve_language("telugu", ENGLISH_SCRIPT) == "telugu"


def test_playlist_override_and_focus_keyword_reach_the_kit():
    kit, llm_call = _generate(_response(), playlist="My Space Playlist", focus_keyword="event horizon")

    assert kit.playlist == "My Space Playlist"
    assert '"My Space Playlist"' in kit.description
    assert "event horizon" in llm_call.call_args.kwargs["prompt"]


def test_kids_audience_changes_prompt_and_follow_line():
    kit, llm_call = _generate(_response(), audience="kids")

    assert "children" in llm_call.call_args.kwargs["prompt"]
    assert "for a new fun science fact every day" in kit.description


@pytest.mark.parametrize(
    "response",
    ["Error: quota exceeded", "not json at all", json.dumps({"titles": []})],
)
def test_unusable_llm_response_falls_back_to_a_basic_kit(response):
    kit, llm_call = _generate(response, subject="What Happens Inside a Black Hole")

    assert kit.used_fallback
    assert llm_call.call_count == 2
    assert kit.titles == ["What Happens Inside a Black Hole"]
    assert "📖 The full fact:" in kit.description
    assert kit.hashtags[-1] == "#Shorts"
    assert "wonder vault" in kit.tags


def test_llm_exception_falls_back_without_raising():
    with patch.object(yk.llm, "_generate_response", side_effect=RuntimeError("network down")):
        kit = yk.generate_kit(TELUGU_SCRIPT)

    assert kit.used_fallback
    assert kit.language == "telugu"
    assert kit.titles and kit.description


def test_script_or_subject_is_required():
    with pytest.raises(ValueError):
        yk.generate_kit("   ", subject="")


def test_markdown_export_contains_every_section():
    kit, _ = _generate(_response())
    markdown = kit.to_markdown()

    for heading in ("## Title options", "## Description", "## Tags", "## Keywords", "## Hashtags", "## Pinned comment", "## Playlist"):
        assert heading in markdown
