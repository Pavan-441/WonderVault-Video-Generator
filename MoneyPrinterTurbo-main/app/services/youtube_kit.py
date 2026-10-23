"""YouTube Kit: upload-ready title options, description, tags, keywords and hashtags.

The LLM writes the creative parts (titles, the search-friendly hook, the closing
question, tags and keywords). The description itself is assembled here so it
always follows the WonderVault upload-kit structure, contains the narration
exactly as written, and stays inside YouTube's limits even when the model
ignores an instruction. If the LLM is unavailable a basic kit is built from the
script so the user still gets something to edit.
"""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List

from loguru import logger

from app.services import llm, thumbnail
from app.utils import utils

# YouTube limits (YouTube Studio rejects anything longer).
TITLE_MAX_CHARS = 100
DESCRIPTION_MAX_CHARS = 5000
TAGS_MAX_CHARS = 500
# Titles longer than this get cut off in the Shorts feed and search results.
TITLE_TARGET_CHARS = {"shorts": 60, "long": 70}
# Telugu titles carry an English search suffix ("| Black Hole Explained in
# Telugu"); the script libraries' Telugu titles run 66-70 characters.
TELUGU_TITLE_EXTRA_CHARS = 12
# Only the first three hashtags show above the title; more than 60 makes
# YouTube ignore all of them. Five matches the script libraries.
HASHTAG_COUNT = 5
TITLE_OPTION_COUNT = 3
MAX_SCRIPT_CHARS = 4000

LANGUAGES = ("auto", "english", "telugu")
FORMATS = ("shorts", "long")
AUDIENCES = ("general", "kids")
DEFAULT_CHANNEL = "@WonderVault-W"

_SEPARATOR_LINE = re.compile(r"[-*_]{3,}")
_ANGLE_BRACKETS = re.compile(r"[<>]")
_WHITESPACE = re.compile(r"\s+")


@dataclass
class YouTubeKit:
    titles: List[str]
    description: str
    tags: List[str]
    keywords: List[str]
    hashtags: List[str]
    pinned_comment: str
    playlist: str
    language: str
    video_format: str
    used_fallback: bool = False
    warnings: List[str] = field(default_factory=list)

    @property
    def tags_text(self) -> str:
        return ", ".join(self.tags)

    def to_markdown(self) -> str:
        titles = "\n".join(
            f"{index}. {title} ({len(title)} chars)"
            for index, title in enumerate(self.titles, start=1)
        )
        return (
            "# YouTube Upload Kit\n\n"
            f"## Title options\n{titles}\n\n"
            f"## Description\n```text\n{self.description}\n```\n\n"
            f"## Tags ({tags_character_count(self.tags)}/{TAGS_MAX_CHARS} chars)\n"
            f"{self.tags_text}\n\n"
            f"## Keywords\n{', '.join(self.keywords)}\n\n"
            f"## Hashtags\n{' '.join(self.hashtags)}\n\n"
            f"## Pinned comment\n{self.pinned_comment}\n\n"
            f"## Playlist\n{self.playlist}\n"
        )


def title_target(video_format: str, language: str) -> int:
    extra = TELUGU_TITLE_EXTRA_CHARS if language == "telugu" else 0
    return min(TITLE_MAX_CHARS, TITLE_TARGET_CHARS[video_format] + extra)


def brand_tag(channel: str) -> str:
    """'@WonderVault-W' -> 'wonder vault': the channel name as people would search it."""
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", channel or "") if len(p) > 1]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", " ".join(parts)).lower()


def resolve_language(language: str, script: str, subject: str = "") -> str:
    value = (language or "auto").strip().lower()
    if value in ("english", "telugu"):
        return value
    return "telugu" if thumbnail.is_telugu(script) or thumbnail.is_telugu(subject) else "english"


def clean_script(script: str) -> str:
    """The narration as viewers should read it: no pause tags or separator lines."""
    text = utils.remove_pause_tags(script or "")
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not _SEPARATOR_LINE.fullmatch(line)]
    return "\n".join(lines).strip()


def _plain(text) -> str:
    """One line, no angle brackets (YouTube rejects < and > in titles and descriptions)."""
    return _WHITESPACE.sub(" ", _ANGLE_BRACKETS.sub("", str(text or ""))).strip()


def clamp_title(title: str) -> str:
    # Hashtags in the title push the hook out of view; the description carries them.
    title = _plain(re.sub(r"(^|\s)#[^\s#]+", " ", str(title or "")))
    if len(title) <= TITLE_MAX_CHARS:
        return title
    cut = title[:TITLE_MAX_CHARS]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" |:-,")


def tags_character_count(tags: List[str]) -> int:
    """Characters YouTube counts: tags with spaces are quoted, plus a comma between tags."""
    if not tags:
        return 0
    return sum(len(tag) + (2 if " " in tag else 0) for tag in tags) + len(tags) - 1


def normalize_tags(raw, max_chars: int = TAGS_MAX_CHARS) -> List[str]:
    if isinstance(raw, str):
        candidates = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        candidates = [str(item) for item in raw]
    else:
        candidates = []

    seen = set()
    tags: List[str] = []
    for candidate in candidates:
        tag = _plain(candidate.replace("#", "")).strip(" ,").lower()
        if not tag or tag in seen or len(tag) > TITLE_MAX_CHARS:
            continue
        if tags_character_count(tags + [tag]) > max_chars:
            continue
        seen.add(tag)
        tags.append(tag)
    return tags


def normalize_keywords(raw, limit: int = 12) -> List[str]:
    candidates = raw.split(",") if isinstance(raw, str) else list(raw or [])
    seen = set()
    keywords: List[str] = []
    for candidate in candidates:
        keyword = _plain(str(candidate).replace("#", "")).strip(" ,")
        if not keyword or keyword.lower() in seen:
            continue
        seen.add(keyword.lower())
        keywords.append(keyword)
        if len(keywords) >= limit:
            break
    return keywords


def _hashtag_body(text: str) -> str:
    # Keep letters, digits and combining marks: Telugu vowel signs are category M,
    # which Python's \w (and so llm._normalize_hashtags) would strip out.
    return "".join(
        ch for ch in str(text) if ch == "_" or unicodedata.category(ch)[0] in "LNM"
    )


def normalize_hashtags(raw, video_format: str) -> List[str]:
    """Topic hashtags first; Shorts always end with #Shorts."""
    candidates = re.split(r"[\s,]+", raw) if isinstance(raw, str) else list(raw or [])
    seen = set()
    tags: List[str] = []
    for candidate in candidates:
        body = _hashtag_body(candidate)
        if not body or body.lower() in seen or body.lower() == "shorts":
            continue
        seen.add(body.lower())
        tags.append(f"#{body}")
    if video_format == "shorts":
        return tags[: HASHTAG_COUNT - 1] + ["#Shorts"]
    return tags[:HASHTAG_COUNT]


def _description_lines(language: str, video_format: str, audience: str, channel: str, playlist: str):
    fact = "mind-blowing fact" if audience == "general" else "fun science fact"
    if language == "telugu":
        heading = "📖 ఈ Short లో:" if video_format == "shorts" else "📖 ఈ వీడియోలో:"
        playlist_line = f'📺 ఇలాంటి మరిన్ని వీడియోలు: "{playlist}" Playlist చూడండి.' if playlist else ""
        follow_line = f"🔔 ప్రతిరోజూ ఒక కొత్త సైన్స్ ఫ్యాక్ట్ కోసం {channel} ని Follow అవ్వండి!"
    else:
        heading = "📖 The full fact:" if video_format == "shorts" else "📖 In this video:"
        playlist_line = f'📺 More like this: the "{playlist}" playlist on {channel}.' if playlist else ""
        follow_line = f"🔔 Follow {channel} for a new {fact} every day."
    return heading, playlist_line, follow_line


def build_description(
    *,
    hook: str,
    script: str,
    question: str,
    hashtags: List[str],
    language: str,
    video_format: str,
    audience: str,
    channel: str,
    playlist: str,
    english_summary: str = "",
    romanized_search: str = "",
) -> str:
    heading, playlist_line, follow_line = _description_lines(
        language, video_format, audience, channel, playlist
    )
    top = [_plain(hook)]
    if language == "telugu":
        # English summary and a Romanized search line catch viewers who type the
        # question in English or in Telugu written with English letters.
        if english_summary:
            top.append(_plain(english_summary))
        if romanized_search:
            top.append(f"🔎 {_plain(romanized_search)}")

    tail_blocks = []
    if question:
        tail_blocks.append(f"💬 {_plain(question)}")
    tail_blocks.append("\n".join(line for line in (playlist_line, follow_line) if line))
    if hashtags:
        tail_blocks.append(" ".join(hashtags))

    head = "\n".join(line for line in top if line)
    tail = "\n\n".join(tail_blocks)
    narration = _ANGLE_BRACKETS.sub("", script)
    # Keep the whole kit within YouTube's limit by shortening only the narration.
    budget = DESCRIPTION_MAX_CHARS - len(head) - len(heading) - len(tail) - 8
    if len(narration) > budget:
        narration = narration[: max(0, budget - 1)].rstrip() + "…"
    blocks = [head, f"{heading}\n{narration}" if narration else "", tail]
    return "\n\n".join(block for block in blocks if block).strip()


def build_prompt(
    *,
    script: str,
    subject: str,
    language: str,
    video_format: str,
    audience: str,
    channel: str,
    focus_keyword: str,
) -> str:
    target = title_target(video_format, language)
    brand = brand_tag(channel) or "science facts"
    if language == "telugu":
        brand = f"{brand} telugu"
    topic_hashtags = HASHTAG_COUNT - 1 if video_format == "shorts" else HASHTAG_COUNT
    format_label = "YouTube Short (vertical, under 3 minutes)" if video_format == "shorts" else "regular long-form YouTube video"
    audience_rule = (
        "The audience is children and families: keep every line simple, positive and safe for kids."
        if audience == "kids"
        else "The audience is curious general viewers."
    )
    if language == "telugu":
        language_rules = (
            "- Write titles, description_hook, question and pinned_comment in Telugu. Natural Tenglish is fine: "
            "keep common English science words (Black Hole, DNA, NASA) in English.\n"
            "- Each title is the Telugu hook with its one emoji, then ' | ' plus an English search phrase such as "
            "'Black Hole Explained in Telugu'. The English part must not repeat the Telugu part.\n"
            '- "english_summary": one English sentence like "What happens if you fall into a black hole — explained in Telugu."\n'
            '- "romanized_search": the main question in Telugu words typed with English letters, the way Telugu '
            'viewers type it into YouTube search, e.g. "black hole lo padithe em avuthundi" or '
            '"aakasham enduku neelam ga untundi". It must be Telugu words, not an English phrase.\n'
            "- Tags mix Romanized Telugu searches, English searches, 'in telugu' variants and broad Telugu tags "
            "(telugu facts, telugu science).\n"
            "- One hashtag is #TeluguFacts or #Telugu."
        )
    else:
        language_rules = (
            "- Write everything in English.\n"
            '- "english_summary" and "romanized_search" are empty strings.'
        )
    focus = f"\nThe creator wants to rank for this keyword, use it where it fits naturally: {focus_keyword}" if focus_keyword else ""
    example = json.dumps(
        {
            "titles": ["...", "...", "..."],
            "description_hook": "...",
            "english_summary": "",
            "romanized_search": "",
            "question": "...",
            "pinned_comment": "...",
            "playlist": "...",
            "hashtags": ["#Topic", "#..."],
            "tags": ["main search phrase", "..."],
            "keywords": ["main search phrase", "..."],
        },
        ensure_ascii=False,
    )
    return f"""You are a YouTube growth strategist for the science channel {channel}. Write the upload metadata that gives this {format_label} the best chance to be found in search and suggested to new viewers.

Rules:
- Titles: exactly {TITLE_OPTION_COUNT} clearly different options (1: search-first, 2: curiosity hook, 3: surprising/emotional). Start with the name people actually search for, then the hook. Exactly one emoji in every title, no hashtags, under {target} characters, never over {TITLE_MAX_CHARS}. Honest: never promise something the video does not show.
- "description_hook": the first 1-2 sentences of the description. Include the main search keyword naturally. This is what search results show. No hashtags.
- "question": one specific, easy-to-answer question for the comments (yes/no or pick one).
- "pinned_comment": the question rephrased for a pinned comment, ending with 👇.
- "playlist": a short playlist name for this topic's category.
- "hashtags": exactly {topic_hashtags} hashtags in English letters, without spaces. The first is the specific topic in CamelCase (e.g. #BlackHole), then broader ones. Do not include #Shorts or the channel name.
- "tags": 10-15 lowercase YouTube tags, together under 450 characters. First the exact main search phrase, then specific topic terms and names from the script, then broad category tags, and last exactly this brand tag: "{brand}".
- "keywords": 8-12 search phrases people type into YouTube for this topic, most searched first. These guide titles and descriptions, so no keyword stuffing.
- Use only facts that are in the script. Do not invent numbers or claims.
- {audience_rule}
{language_rules}{focus}

Return ONLY one JSON object in exactly this shape, no markdown: {example}

Video subject: {subject or "(not given)"}
Video script:
{script}"""


def _parse(response: str) -> dict:
    try:
        data = json.loads(llm._strip_code_fence(response))
    except Exception:
        match = re.search(r"\{.*\}", response or "", re.DOTALL)
        data = json.loads(match.group()) if match else None
    if not isinstance(data, dict):
        raise ValueError("YouTube kit response is not a JSON object")
    return data


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?।])\s+", text.strip(), maxsplit=1)[0] if text.strip() else ""


def _fallback_fields(script: str, subject: str, language: str, channel: str) -> dict:
    base = _plain(subject) or _plain(_first_sentence(script))
    # Split on whitespace, not \W: Telugu vowel signs count as \W in Python regex.
    words = [w.strip(".,!?;:'\"()[]।") for w in base.split()]
    words = [w for w in words if len(w) >= 3]
    ascii_words = [w for w in words if w.isascii() and w.isalpha()]
    topic = "".join(word[:1].upper() + word[1:] for word in ascii_words[:2])
    brand = brand_tag(channel)
    broad_tags = ["science facts", "did you know", "science shorts"]
    hashtags = [f"#{topic}"] if topic else []
    if language == "telugu":
        broad_tags = ["telugu facts", "telugu science", "science facts telugu"]
        hashtags.append("#TeluguFacts")
    hashtags += ["#ScienceFacts", "#Facts", "#Science"]
    return {
        "titles": [base],
        "description_hook": _plain(_first_sentence(script)) or base,
        "question": "",
        "pinned_comment": "",
        "playlist": "",
        "hashtags": hashtags,
        "tags": [base.lower(), *[w.lower() for w in words[:4]], *broad_tags, brand],
        "keywords": [base, *words[:4]],
    }


def generate_kit(
    script: str,
    subject: str = "",
    language: str = "auto",
    video_format: str = "shorts",
    audience: str = "general",
    channel: str = DEFAULT_CHANNEL,
    playlist: str = "",
    focus_keyword: str = "",
) -> YouTubeKit:
    """Generate an upload kit. Never raises because of a bad or missing LLM response."""
    narration = clean_script(script)[:MAX_SCRIPT_CHARS]
    subject = _plain(subject)
    if not narration and not subject:
        raise ValueError("a script or a video subject is required")
    video_format = video_format if video_format in FORMATS else "shorts"
    audience = audience if audience in AUDIENCES else "general"
    channel = _plain(channel) or DEFAULT_CHANNEL
    resolved_language = resolve_language(language, narration, subject)

    prompt = build_prompt(
        script=narration or subject,
        subject=subject,
        language=resolved_language,
        video_format=video_format,
        audience=audience,
        channel=channel,
        focus_keyword=_plain(focus_keyword),
    )
    data = None
    for attempt in range(2):
        try:
            response = llm._generate_response(prompt=prompt)
            if isinstance(response, str) and response.startswith("Error: "):
                raise ValueError(response)
            data = _parse(response)
            if any(clamp_title(t) for t in data.get("titles") or []):
                break
            raise ValueError("YouTube kit response has no titles")
        except Exception as exc:
            data = None
            logger.warning(f"YouTube kit generation failed (attempt {attempt + 1}): {exc}")

    used_fallback = data is None
    if used_fallback:
        data = _fallback_fields(narration, subject, resolved_language, channel)

    raw_titles = data.get("titles") or []
    if isinstance(raw_titles, str):
        raw_titles = [raw_titles]
    titles = []
    for raw in raw_titles:
        title = clamp_title(raw)
        if title and title not in titles:
            titles.append(title)
    titles = titles[:TITLE_OPTION_COUNT] or [clamp_title(subject or _first_sentence(narration))]

    hashtags = normalize_hashtags(data.get("hashtags"), video_format)
    question = _plain(data.get("question"))
    final_playlist = _plain(playlist) or _plain(data.get("playlist"))
    description = build_description(
        hook=_plain(data.get("description_hook")) or titles[0],
        script=narration,
        question=question,
        hashtags=hashtags,
        language=resolved_language,
        video_format=video_format,
        audience=audience,
        channel=channel,
        playlist=final_playlist,
        english_summary=_plain(data.get("english_summary")),
        romanized_search=_plain(data.get("romanized_search")),
    )
    pinned = _plain(data.get("pinned_comment")) or (f"{question} 👇" if question else "")

    warnings = []
    target = title_target(video_format, resolved_language)
    if any(len(title) > target for title in titles):
        warnings.append(f"Some titles are longer than {target} characters and may be cut off in the feed.")

    return YouTubeKit(
        titles=titles,
        description=description,
        tags=normalize_tags(data.get("tags")),
        keywords=normalize_keywords(data.get("keywords")),
        hashtags=hashtags,
        pinned_comment=pinned,
        playlist=final_playlist,
        language=resolved_language,
        video_format=video_format,
        used_fallback=used_fallback,
        warnings=warnings,
    )
