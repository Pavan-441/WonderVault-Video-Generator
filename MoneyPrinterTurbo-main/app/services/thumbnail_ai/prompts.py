"""Google Flow (Nano Banana) prompts for finished YouTube thumbnails.

The prompts are copied into Google Flow by the user, so they are written as
clear, sectioned instructions that describe the *whole* thumbnail: subject,
scene, exact text with typography and placement, layout and quality.
"""

import re

# Each style is a distinct creative direction with its own typography, so the
# user gets real choices rather than three near-identical images.
STYLE_VARIANTS = {
    "shock_reaction": {
        "label": "Shock Reaction",
        "direction": (
            "Viral reaction-style thumbnail. The main subject is large, with a genuine, "
            "exaggerated expression of shock or awe. The key object of the story is shown "
            "big and dramatic beside them. Bright, saturated background with strong contrast. "
            "One bold red arrow or red circle points at the most surprising detail."
        ),
        "headline_style": (
            "huge heavy condensed sans-serif, ALL CAPS, bright yellow fill, thick black "
            "outline and a hard drop shadow"
        ),
        "subtitle_style": "bold white text on a red rounded banner",
        "badge_style": "small black rounded pill with white text",
    },
    "cinematic_mystery": {
        "label": "Cinematic Mystery",
        "direction": (
            "Dark, moody, cinematic movie-poster thumbnail. Dramatic rim lighting, a glowing "
            "focal point, teal-and-orange color grade, volumetric light rays and deep shadows. "
            "The subject looks intrigued or serious. Builds mystery and curiosity."
        ),
        "headline_style": (
            "huge bold serif-free display letters with a bright white-to-gold gradient, "
            "subtle outer glow and a dark outline"
        ),
        "subtitle_style": "clean light-gray letters with a soft glow",
        "badge_style": "small dark translucent pill with gold text",
    },
    "bold_explainer": {
        "label": "Bold Explainer",
        "direction": (
            "Clean, bright, high-energy explainer thumbnail. Simple vivid gradient background, "
            "the subject on one side and a large, clear illustration of the topic on the other, "
            "with a few bold graphic accents: a glowing outline around the subject, simple "
            "icons, or a split-screen comparison feel. Understandable in one glance."
        ),
        "headline_style": (
            "huge heavy rounded sans-serif, white fill with a thick dark outline, the single "
            "most important word highlighted in a contrasting color block"
        ),
        "subtitle_style": "bold dark text on a bright yellow rounded block",
        "badge_style": "small white rounded pill with dark text",
    },
}

DEFAULT_STYLE_KEYS = list(STYLE_VARIANTS)

_FORMAT = {
    "16:9": "Aspect ratio 16:9, landscape, 1280x720 YouTube video thumbnail.",
    "9:16": "Aspect ratio 9:16, vertical, 1080x1920 YouTube Shorts cover.",
}

# Text sits on one side and the subject on the other, so neither covers the other.
_LAYOUT = {
    "16:9": (
        "Text block on the left half; main subject on the right half, cropped from the "
        "chest up and filling most of the height."
    ),
    "9:16": (
        "Text block in the top third; main subject large in the middle; keep the bottom "
        "quarter simple."
    ),
}

# Empty area kept for text that the app adds after upload (overlay mode). Matches
# where app.services.thumbnail.compose_thumbnail draws text without a cutout.
_TEXT_ZONE = {
    "16:9": "the upper-left area (top half, left two-thirds of the image)",
    "9:16": "the top third of the image",
}

# YouTube draws its own UI over these regions.
_SAFE_ZONE = {
    "16:9": (
        "Keep faces, objects and text out of the bottom-right corner, where YouTube shows "
        "the video duration."
    ),
    "9:16": (
        "Keep faces, objects and text out of the bottom quarter and the right edge, where "
        "the Shorts title and buttons appear."
    ),
}

_QUALITY = (
    "Professional, high-end YouTube thumbnail. Photorealistic, sharp focus, studio-quality "
    "lighting, very high contrast and saturated colors. One clear focal point and at most "
    "three visual elements. Must stay readable when shown very small on a phone."
)

_AVOID = (
    "No watermarks, no logos, no borders, no extra or misspelled words, no gibberish text, "
    "no blurry or distorted faces, no clutter."
)


def summarize_topic(script: str, limit: int = 320) -> str:
    """Condense a narration script into a short topic description for the prompt."""
    text = re.sub(r"\s+", " ", (script or "")).strip()
    if len(text) <= limit:
        return text
    sentences = re.split(r"(?<=[.!?।])\s+", text)
    summary = ""
    for sentence in sentences:
        if len(summary) + len(sentence) + 1 > limit:
            break
        summary = f"{summary} {sentence}".strip()
    return summary or text[:limit]


def _quoted(text: str) -> str:
    return '"' + text.replace('"', "'").strip() + '"'


def _has_telugu(*texts: str) -> bool:
    return any(re.search(r"[ఀ-౿]", t or "") for t in texts)


def build_prompt(
    topic: str,
    title: str,
    aspect_ratio: str = "16:9",
    style_key: str = "shock_reaction",
    subtitle: str = "",
    badge_text: str = "",
    has_reference: bool = False,
    render_text: bool = True,
    scene: str = "",
) -> str:
    """Build a complete, copy-ready Google Flow prompt for one thumbnail.

    ``render_text`` False is overlay mode: Flow leaves a clean area and the app
    draws the text after the image is uploaded back.
    """
    style = STYLE_VARIANTS.get(style_key, STYLE_VARIANTS["shock_reaction"])
    lines = [
        "Create a finished, professional YouTube thumbnail.",
        _FORMAT.get(aspect_ratio, _FORMAT["16:9"]),
        "",
        f"TOPIC: {topic}",
        f"STYLE: {style['direction']}",
    ]
    if scene:
        lines.append(f"SCENE: {scene.strip()}")
    if has_reference:
        lines.append(
            "SUBJECT: Use the person or object from the attached photo as the main subject. "
            "Keep their face, identity and key features exactly recognisable. Cut them out "
            "cleanly from the original background, relight them to match this scene, and "
            "add a subtle bright outline glow so they pop off the background."
        )

    lines.append("")
    if render_text and title:
        headline_style = style["headline_style"]
        if _has_telugu(title):
            # Telugu has no letter case; asking for caps only confuses the model.
            headline_style = headline_style.replace(" ALL CAPS,", "")
        lines.append("TEXT (render exactly as written, nothing else):")
        lines.append(
            f"- Headline: {_quoted(title)} — {headline_style}, covering about a third "
            "of the image."
        )
        if subtitle:
            lines.append(f"- Secondary line: {_quoted(subtitle)} — {style['subtitle_style']}, under the headline.")
        if badge_text:
            lines.append(f"- Corner badge: {_quoted(badge_text)} — {style['badge_style']}, in the top corner.")
        if _has_telugu(title, subtitle, badge_text):
            lines.append(
                "- The text is in Telugu script: reproduce every Telugu letter exactly, with "
                "correct conjuncts (vottulu) and vowel signs (gunintalu). Do not translate or "
                "transliterate it."
            )
        lines.append(f"LAYOUT: {_LAYOUT.get(aspect_ratio, _LAYOUT['16:9'])}")
    else:
        zone = _TEXT_ZONE.get(aspect_ratio, _TEXT_ZONE["16:9"])
        lines.append(
            "TEXT: Do NOT render any text, letters or numbers anywhere. Leave "
            f"{zone} visually simple and slightly darker, with no faces or important objects, "
            "because the title will be added on top later. Place the main subject in the "
            "remaining area."
        )
    lines.append(f"SAFE ZONE: {_SAFE_ZONE.get(aspect_ratio, _SAFE_ZONE['16:9'])}")
    lines.append(f"QUALITY: {_QUALITY}")
    lines.append(f"AVOID: {_AVOID}")
    return "\n".join(lines)


def build_edit_prompt(
    instruction: str,
    aspect_ratio: str = "16:9",
    has_reference: bool = False,
    text_lines: tuple = (),
    render_text: bool = True,
) -> str:
    """Flow prompt for editing an existing thumbnail (attached as the first image)."""
    lines = [
        "Edit the attached YouTube thumbnail image.",
        f"CHANGE: {instruction.strip()}",
        "KEEP: everything the change does not mention exactly as it is — composition, "
        "subject, faces, colors, style and text. Keep the same aspect ratio "
        f"({aspect_ratio}) and framing.",
    ]
    if has_reference:
        lines.append(
            "SUBJECT: The other attached photo shows the main subject; keep their face and "
            "identity matching it."
        )
    texts = [t for t in text_lines if t]
    if render_text and texts:
        quoted = ", ".join(_quoted(t) for t in texts)
        lines.append(f"TEXT: All text must stay spelled exactly as: {quoted}. Do not add other words.")
    elif not render_text:
        zone = _TEXT_ZONE.get(aspect_ratio, _TEXT_ZONE["16:9"])
        lines.append(f"TEXT: Do NOT add any text. Keep {zone} simple and free of important objects.")
    lines.append(f"SAFE ZONE: {_SAFE_ZONE.get(aspect_ratio, _SAFE_ZONE['16:9'])}")
    return "\n".join(lines)
