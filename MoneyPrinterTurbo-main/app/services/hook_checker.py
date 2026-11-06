"""Hook checker: tests a script against the Shorts playbook before rendering.

Local checks are free and instant (length, opening phrase, closing question, outro).
The AI part scores the first line and writes stronger opening lines.
"""

import json
import re
from dataclasses import dataclass, field

from loguru import logger

from app.services import bilingual, llm, scene_planner
from app.utils import utils

IDEAL_SECONDS = (25.0, 40.0)
MAX_SHORT_SECONDS = 60.0
MAX_HOOK_SECONDS = 4.5
HOOK_COUNT = 3

OK = "ok"
WARN = "warn"

# Openings the playbook rules out: greetings, "did you know", channel intros.
_WEAK_OPENINGS = (
    "did you know",
    "hey guys",
    "hi guys",
    "hello everyone",
    "welcome",
    "in this video",
    "today we",
    "today i",
    "have you ever wondered",
    "నమస్కారం",
    "హాయ్ ఫ్రెండ్స్",
    "హలో ఫ్రెండ్స్",
    "ఈ వీడియోలో",
    "మీకు తెలుసా",
)
_OUTRO_WORDS = ("subscribe", "సబ్స్క్రైబ్", "సబ్‌స్క్రైబ్", "like and share", "bell icon")
_SEPARATOR_LINE = re.compile(r"^[\s\-*_=~#]+$")
_FIRST_SENTENCE = re.compile(r"^(.+?[.!?।॥](?=\s|$)|[^\n]+)", re.DOTALL)


@dataclass
class Check:
    key: str
    level: str  # OK or WARN
    values: dict = field(default_factory=dict)


@dataclass
class HookReport:
    checks: list[Check]
    seconds: float
    hook: str
    score: int | None = None
    reason: str = ""
    hooks: list[str] = field(default_factory=list)
    ai_error: str = ""


def _clean(script: str) -> str:
    lines = [
        line.strip()
        for line in utils.remove_pause_tags(script or "").splitlines()
        if line.strip() and not _SEPARATOR_LINE.match(line.strip())
    ]
    return "\n".join(lines)


def first_sentence(script: str) -> str:
    text = _clean(script)
    match = _FIRST_SENTENCE.match(text)
    return match.group(1).strip() if match else ""


def replace_hook(script: str, new_hook: str) -> str:
    """Swaps the script's first sentence for new_hook and keeps everything else as written."""
    hook = first_sentence(script)
    if not hook or hook not in script:
        return f"{new_hook.strip()} {script.strip()}".strip()
    return script.replace(hook, new_hook.strip(), 1)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?।॥])\s+|\n+", text) if s.strip()]


def local_checks(script: str, voice_rate: float = 1.0) -> HookReport:
    text = _clean(script)
    rate = voice_rate if voice_rate and voice_rate > 0 else 1.0
    sentences = _sentences(text)
    seconds = (
        scene_planner.speech_seconds(text) + max(len(sentences) - 1, 0) * scene_planner.SENTENCE_PAUSE_SECONDS
    ) / rate
    hook = first_sentence(script)
    hook_seconds = scene_planner.speech_seconds(hook) / rate
    lowered_hook = hook.lower()
    ending = " ".join(sentences[-2:]).lower()

    checks = []
    low, high = IDEAL_SECONDS
    if seconds < low:
        checks.append(Check("length_short", WARN, {"seconds": round(seconds)}))
    elif seconds <= high:
        checks.append(Check("length_ideal", OK, {"seconds": round(seconds)}))
    elif seconds <= MAX_SHORT_SECONDS:
        checks.append(Check("length_long", WARN, {"seconds": round(seconds)}))
    else:
        checks.append(Check("length_too_long", WARN, {"seconds": round(seconds)}))

    weak = next((p for p in _WEAK_OPENINGS if lowered_hook.startswith(p)), "")
    if weak:
        checks.append(Check("hook_weak_opening", WARN, {"phrase": weak}))
    if "wonder vault" in lowered_hook:
        checks.append(Check("hook_channel_name", WARN))
    if hook_seconds > MAX_HOOK_SECONDS:
        checks.append(Check("hook_long", WARN, {"seconds": round(hook_seconds, 1)}))
    if not weak and hook_seconds <= MAX_HOOK_SECONDS and "wonder vault" not in lowered_hook:
        checks.append(Check("hook_ok", OK))

    if "?" in ending:
        checks.append(Check("question_ok", OK))
    else:
        checks.append(Check("question_missing", WARN))
    if any(word in ending for word in _OUTRO_WORDS):
        checks.append(Check("outro", WARN))
    return HookReport(checks=checks, seconds=round(seconds, 1), hook=hook)


def build_prompt(script: str, hook: str, subject: str) -> str:
    language = "Telugu/Tenglish (Telugu script, science terms in English)" if bilingual.detect(script) == bilingual.TELUGU else "English"
    return f"""You coach the @WonderVault-W science Shorts channel on hooks.

The hook is the first sentence. A strong hook states the most surprising fact or a
vivid "what if" straight away, in under 4 seconds of speech, with no greeting, no
channel name and no "Did you know".

Score the current hook from 1 (viewers scroll away) to 10 (impossible to skip), give
one short reason, and write {HOOK_COUNT} stronger first sentences in {language}. Each must
lead naturally into the rest of the script, keep the facts true, and be at most 15 words.

Video subject: {subject or "(see script)"}
Current hook: {hook}

Full script:
{script}

Return only JSON: {{"score": 7, "reason": "...", "hooks": ["...", "...", "..."]}}"""


def _parse(response: str) -> dict:
    text = llm._strip_code_fence(response)
    try:
        data = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(match.group()) if match else None
    if not isinstance(data, dict):
        raise ValueError("hook response is not a JSON object")
    return data


def check(script: str, subject: str = "", voice_rate: float = 1.0, app_config=None) -> HookReport:
    if not _clean(script):
        raise ValueError("a script is required")
    report = local_checks(script, voice_rate)
    prompt = build_prompt(_clean(script), report.hook, subject)
    response = (
        llm._generate_response(prompt=prompt)
        if app_config is None
        else llm._generate_response(prompt=prompt, app_config=app_config)
    )
    try:
        if not response or response.startswith("Error: "):
            raise ValueError(response or "empty response")
        data = _parse(response)
        score = int(data.get("score"))
        report.score = min(max(score, 1), 10)
        report.reason = " ".join(str(data.get("reason") or "").split())
        report.hooks = [
            " ".join(str(h or "").split()) for h in (data.get("hooks") or []) if str(h or "").strip()
        ][:HOOK_COUNT]
    except (ValueError, TypeError) as exc:
        logger.warning(f"hook check AI part failed: {exc}")
        report.ai_error = str(exc)
    return report
