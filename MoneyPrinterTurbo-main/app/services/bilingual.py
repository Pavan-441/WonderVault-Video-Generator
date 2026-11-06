"""English <-> Telugu/Tenglish versions of a script, plus the voice settings saved per language."""

import json
import re

from loguru import logger

from app.services import llm

ENGLISH = "english"
TELUGU = "telugu"
LANGUAGES = (ENGLISH, TELUGU)
# Script Language values in the Video Generator.
VIDEO_LANGUAGE = {ENGLISH: "en-US", TELUGU: "te-IN-Mix"}
PROFILE_FIELDS = ("tts_server", "voice_name", "voice_rate", "font_name", "video_language")
MAX_ATTEMPTS = 2

_TELUGU_CHARS = re.compile(r"[ఀ-౿]")


class TranslationError(RuntimeError):
    pass


def detect(text: str) -> str:
    return TELUGU if _TELUGU_CHARS.search(text or "") else ENGLISH


def other(language: str) -> str:
    return ENGLISH if language == TELUGU else TELUGU


def clean_profile(raw) -> dict | None:
    """A saved language profile, or None when it has no voice."""
    if not isinstance(raw, dict) or not str(raw.get("voice_name") or "").strip():
        return None
    profile = {field: raw.get(field) for field in PROFILE_FIELDS}
    try:
        profile["voice_rate"] = float(profile.get("voice_rate") or 1.0)
    except (TypeError, ValueError):
        profile["voice_rate"] = 1.0
    return profile


def get_profile(ui_config: dict, language: str) -> dict | None:
    return clean_profile((ui_config.get("language_profiles") or {}).get(language))


def set_profile(ui_config: dict, language: str, profile: dict) -> dict:
    """Returns the updated profiles table, ready to save into the [ui] config."""
    profiles = dict(ui_config.get("language_profiles") or {})
    profiles[language] = {field: profile.get(field) for field in PROFILE_FIELDS}
    return profiles


_STYLE = {
    TELUGU: (
        "conversational Telugu written in Telugu script, the way a Telugu YouTuber talks "
        "(Tenglish). Keep science and technical terms in English letters (Black Hole, DNA, "
        "Gravity, Brain, neurons). Do not use formal or textbook Telugu."
    ),
    ENGLISH: "simple, punchy spoken English for YouTube Shorts narration.",
}


def build_prompt(script: str, subject: str, target: str) -> str:
    return f"""You translate narration scripts for the @WonderVault-W science Shorts channel.

Rewrite the script and subject below in {_STYLE[target]}

Rules:
- Keep every fact, number and unit exactly. Do not add or remove facts.
- Keep the first sentence as the hook and keep the closing question.
- Keep about the same length, so the video stays the same duration.
- Keep "Wonder Vault" and "@WonderVault-W" unchanged.
- Keep pause tags such as [pause 1s] and separator lines such as --- exactly where they are.
- Keep the paragraph breaks.
- It must sound natural when read aloud, not like a word-for-word translation.

Return only JSON: {{"subject": "...", "script": "..."}}

Subject:
{subject or "(none)"}

Script:
{script}"""


def _parse(response: str) -> dict:
    text = llm._strip_code_fence(response)
    try:
        data = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(match.group()) if match else None
    if not isinstance(data, dict):
        raise ValueError("translation response is not a JSON object")
    return data


def translate(script: str, subject: str = "", target: str = TELUGU, app_config=None) -> dict:
    """Returns {"subject", "script", "language"} for the target language."""
    script = (script or "").strip()
    if not script:
        raise ValueError("a script is required")
    if target not in LANGUAGES:
        raise ValueError(f"unsupported language: {target}")

    prompt = build_prompt(script, (subject or "").strip(), target)
    last_error = "no response"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = (
            llm._generate_response(prompt=prompt)
            if app_config is None
            else llm._generate_response(prompt=prompt, app_config=app_config)
        )
        if not response or response.startswith("Error: "):
            last_error = response or "empty response"
            logger.warning(f"script translation attempt {attempt} failed: {last_error}")
            continue
        try:
            data = _parse(response)
        except ValueError as exc:
            last_error = str(exc)
            logger.warning(f"script translation attempt {attempt} unreadable: {exc}")
            continue
        new_script = str(data.get("script") or "").strip()
        if not new_script or detect(new_script) != target:
            last_error = "the result is not in the requested language"
            logger.warning(f"script translation attempt {attempt}: {last_error}")
            continue
        return {
            "subject": str(data.get("subject") or "").strip() or subject,
            "script": new_script,
            "language": target,
        }
    raise TranslationError(last_error)
