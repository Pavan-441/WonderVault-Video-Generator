"""Scene planner: splits a script into scenes and writes one Google Flow image prompt per scene.

The images come back in scene order, and each one stays on screen for its scene's
share of the narration (see scene_durations).
"""

import json
import re
import unicodedata
from dataclasses import dataclass, field

from loguru import logger

from app.services import llm
from app.utils import utils

MIN_SCENE_SECONDS = 3.0
MAX_SCENE_SECONDS = 7.0
MAX_SCENES = 20
SENTENCE_PAUSE_SECONDS = 0.35
MAX_ATTEMPTS = 2

STYLES = {
    "photo": "photorealistic, cinematic lighting, sharp focus, high detail",
    "cinematic_3d": "cinematic 3D render, dramatic lighting, rich colors, high detail",
    "illustration": "vibrant digital illustration, clean shapes, bold colors",
    "kids_cartoon": "cute colorful cartoon for kids, soft lighting, friendly characters",
}
ASPECTS = {"portrait": "vertical 9:16 composition", "landscape": "horizontal 16:9 composition"}
_NO_TEXT = "no text, no letters, no watermark"

_SEPARATOR_LINE = re.compile(r"^[\s\-*_=~#]+$")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?।॥。！？])\s+|\n+")


@dataclass
class Scene:
    text: str
    seconds: float
    prompt: str = ""


@dataclass
class ScenePlan:
    scenes: list[Scene]
    used_fallback: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_markdown(self, subject: str = "") -> str:
        lines = [f"# Flow image prompts{f' — {subject}' if subject else ''}", ""]
        for number, scene in enumerate(self.scenes, start=1):
            lines += [
                f"## Scene {number} (~{scene.seconds:.1f}s)",
                f"> {scene.text}",
                "",
                "```text",
                scene.prompt,
                "```",
                "",
            ]
        return "\n".join(lines)


def speech_seconds(text: str) -> float:
    """Rough reading time, same rates as the no-voice estimate but without its 3 s minimum."""
    text = text or ""
    cjk = len(re.findall(r"[一-鿿]", text))
    ascii_words = re.findall(r"[A-Za-z0-9]+", text)
    letters = sum(1 for ch in text if unicodedata.category(ch).startswith(("L", "N")))
    other = max(letters - cjk - sum(len(w) for w in ascii_words), 0)
    return cjk / 4.2 + len(ascii_words) / 2.7 + other / 4.0


def _sentences(script: str) -> list[str]:
    lines = [
        line
        for line in utils.remove_pause_tags(script or "").splitlines()
        if line.strip() and not _SEPARATOR_LINE.match(line.strip())
    ]
    return [s.strip() for s in _SENTENCE_BREAK.split("\n".join(lines)) if s.strip()]


def _merge(sentences: list[str], min_seconds: float) -> list[Scene]:
    scenes: list[Scene] = []
    for sentence in sentences:
        seconds = speech_seconds(sentence)
        last = scenes[-1] if scenes else None
        if (
            last
            and last.seconds < min_seconds
            and last.seconds + seconds + SENTENCE_PAUSE_SECONDS <= max(MAX_SCENE_SECONDS, min_seconds)
        ):
            last.text = f"{last.text} {sentence}"
            last.seconds += seconds + SENTENCE_PAUSE_SECONDS
        else:
            scenes.append(Scene(sentence, seconds))
    # A short last sentence (often the closing question) joins the scene before it.
    if len(scenes) > 1 and scenes[-1].seconds < min_seconds / 2:
        tail = scenes.pop()
        scenes[-1].text = f"{scenes[-1].text} {tail.text}"
        scenes[-1].seconds += tail.seconds + SENTENCE_PAUSE_SECONDS
    return scenes


def split_scenes(script: str) -> list[Scene]:
    """About one scene per 3-7 seconds of narration, never more than MAX_SCENES."""
    sentences = _sentences(script)
    if not sentences:
        return []
    min_seconds = MIN_SCENE_SECONDS
    scenes = _merge(sentences, min_seconds)
    while len(scenes) > MAX_SCENES:
        min_seconds += 1.0
        scenes = _merge(sentences, min_seconds)
    for scene in scenes:
        scene.seconds = round(max(scene.seconds, 0.5), 2)
    return scenes


def scene_durations(weights: list[float], total_seconds: float) -> list[float]:
    """Splits the video length between scenes in proportion to their narration."""
    weights = [max(float(w), 0.1) for w in weights]
    total_weight = sum(weights)
    return [total_seconds * w / total_weight for w in weights]


def style_suffix(style: str, aspect: str) -> str:
    return f"{STYLES.get(style, STYLES['photo'])}, {ASPECTS.get(aspect, ASPECTS['portrait'])}, {_NO_TEXT}"


def build_prompt(scenes: list[Scene], subject: str, style: str, audience: str) -> str:
    numbered = "\n".join(f"{n}. {scene.text}" for n, scene in enumerate(scenes, start=1))
    kids = (
        "\n- The audience is young children: friendly, safe and not scary."
        if audience == "kids"
        else ""
    )
    return f"""You plan images for a narrated science YouTube Short. The narration is split into
{len(scenes)} numbered scenes. Write one image prompt per scene for Google Flow (an AI image
generator).

Rules:
- Write every prompt in English, even when the narration is in Telugu.
- Show exactly what the scene's words describe, as one clear, concrete, visible moment.
- Use the same characters, setting and colour palette across scenes so the images look like one video.
- 15 to 40 words per prompt. Describe the subject, the action, the setting and the camera angle.
- Do not ask for any text, captions, letters, numbers, logos or watermarks in the image.
- Do not mention the art style; it is added separately ({STYLES.get(style, STYLES['photo'])}).{kids}

Video subject: {subject or "(see the narration)"}

Scenes:
{numbered}

Return only JSON with exactly {len(scenes)} prompts in scene order: {{"prompts": ["...", "..."]}}"""


def _parse_prompts(response: str, count: int) -> list[str]:
    text = llm._strip_code_fence(response)
    try:
        data = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(match.group()) if match else None
    prompts = data.get("prompts") if isinstance(data, dict) else None
    if not isinstance(prompts, list) or len(prompts) != count:
        raise ValueError(f"expected {count} prompts")
    prompts = [" ".join(str(p or "").split()).rstrip(" .,") for p in prompts]
    if not all(prompts):
        raise ValueError("a prompt is empty")
    return prompts


def plan_scenes(
    script: str,
    subject: str = "",
    style: str = "photo",
    aspect: str = "portrait",
    audience: str = "general",
    app_config=None,
) -> ScenePlan:
    scenes = split_scenes(script)
    if not scenes:
        raise ValueError("a script is required")
    suffix = style_suffix(style, aspect)
    prompt = build_prompt(scenes, subject, style, audience)
    last_error = "no response"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = (
            llm._generate_response(prompt=prompt)
            if app_config is None
            else llm._generate_response(prompt=prompt, app_config=app_config)
        )
        if not response or response.startswith("Error: "):
            last_error = response or "empty response"
        else:
            try:
                for scene, scene_prompt in zip(scenes, _parse_prompts(response, len(scenes))):
                    scene.prompt = f"{scene_prompt}, {suffix}"
                return ScenePlan(scenes)
            except ValueError as exc:
                last_error = str(exc)
        logger.warning(f"scene prompt attempt {attempt} failed: {last_error}")

    for scene in scenes:
        scene.prompt = f"{subject + ': ' if subject else ''}{scene.text}, {suffix}"
    return ScenePlan(scenes, used_fallback=True, warnings=[last_error])
