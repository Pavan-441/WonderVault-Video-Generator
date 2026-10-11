"""Creative brief: plan one distinct, editable thumbnail concept per style."""

import json
import re
from dataclasses import asdict, dataclass
from typing import Optional, Sequence

from loguru import logger

from app.services import thumbnail
from app.services.thumbnail_ai import prompts

HEADLINE_MAX_WORDS = 5


@dataclass
class Concept:
    style_key: str
    headline: str
    subtitle: str = ""
    scene: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Concept":
        return cls(
            style_key=str(data.get("style_key", "")),
            headline=str(data.get("headline", "") or "").strip(),
            subtitle=str(data.get("subtitle", "") or "").strip(),
            scene=str(data.get("scene", "") or "").strip(),
        )


def default_concepts(title: str, subtitle: str = "", style_keys: Optional[Sequence[str]] = None) -> list[Concept]:
    """Concepts that simply reuse the user's own text (no LLM involved)."""
    keys = list(style_keys or prompts.DEFAULT_STYLE_KEYS)
    return [Concept(style_key=key, headline=title.strip(), subtitle=subtitle.strip()) for key in keys]


def _brief_prompt(script: str, title: str, keys: Sequence[str], telugu: bool) -> str:
    language = (
        "Telugu (natural Tenglish is fine; keep common English words like Black Hole in English)"
        if telugu
        else "English"
    )
    styles = "\n".join(f'- "{k}": {prompts.STYLE_VARIANTS[k]["direction"]}' for k in keys)
    example = json.dumps(
        {k: {"headline": "...", "subtitle": "...", "scene": "..."} for k in keys}, ensure_ascii=False
    )
    return (
        "You are a world-class YouTube thumbnail designer known for very high click-through rates.\n"
        "Plan one thumbnail concept for EACH creative style below. Concepts must be clearly "
        "different from each other, honest to the video (no misleading clickbait), and "
        "instantly understandable on a phone.\n\n"
        f"Creative styles:\n{styles}\n\n"
        "For each style return:\n"
        f'- "headline": the big thumbnail text in {language}, {HEADLINE_MAX_WORDS} words maximum, '
        "punchy and curiosity-driving. Must not repeat the video title word for word.\n"
        f'- "subtitle": optional short supporting line in {language} (3 words max) or an empty string.\n'
        '- "scene": one or two English sentences describing exactly what is shown: the main subject, '
        "their expression, the key object, the background, colors and any arrow/circle/glow accents.\n\n"
        f"Return ONLY a JSON object exactly in this shape: {example}\n\n"
        f"Video title idea: {title or '(none)'}\n"
        f"Video script:\n{script[:1500]}"
    )


def _parse(response: str) -> dict:
    from app.services.llm import _strip_code_fence

    try:
        data = json.loads(_strip_code_fence(response))
    except Exception:
        match = re.search(r"\{.*\}", response or "", re.DOTALL)
        data = json.loads(match.group()) if match else None
    if not isinstance(data, dict):
        raise ValueError("thumbnail brief response is not a JSON object")
    return data


def _clamp_words(text: str, limit: int) -> str:
    words = str(text or "").strip().strip('"').split()
    return " ".join(words[:limit])


def generate_concepts(
    script: str,
    title: str = "",
    subtitle: str = "",
    style_keys: Optional[Sequence[str]] = None,
) -> list[Concept]:
    """Ask the configured LLM for one concept per style.

    Any style the LLM fails to cover falls back to the user's own title, so
    this never raises because of a bad LLM response.
    """
    keys = [k for k in (style_keys or prompts.DEFAULT_STYLE_KEYS) if k in prompts.STYLE_VARIANTS]
    fallback = {c.style_key: c for c in default_concepts(title, subtitle, keys)}
    telugu = thumbnail.is_telugu(title) or thumbnail.is_telugu(script)

    try:
        from app.services import llm

        data = _parse(llm._generate_response(prompt=_brief_prompt(script, title, keys, telugu)))
    except Exception as exc:
        logger.warning(f"thumbnail brief generation failed, using your title: {exc}")
        return list(fallback.values())

    concepts = []
    for key in keys:
        item = data.get(key) if isinstance(data.get(key), dict) else {}
        headline = _clamp_words(item.get("headline", ""), HEADLINE_MAX_WORDS)
        if not headline:
            concepts.append(fallback[key])
            continue
        concepts.append(
            Concept(
                style_key=key,
                headline=headline,
                subtitle=_clamp_words(item.get("subtitle", ""), 4),
                scene=str(item.get("scene", "") or "").strip()[:600],
            )
        )
    return concepts
