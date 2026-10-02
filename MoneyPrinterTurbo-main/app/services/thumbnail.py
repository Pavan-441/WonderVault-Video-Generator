import io
import math
import os
import re
from pathlib import Path
from typing import Optional, Tuple, Union

from loguru import logger
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from app.config import config
from app.utils import utils

# Supported aspect ratios and canvas dimensions
ASPECT_RATIOS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
}

# Style color presets: (fill_color, stroke_color, shadow_color)
STYLE_PRESETS = {
    "yellow_black": {
        "name": "Vibrant Yellow",
        "fill": (255, 222, 0),
        "stroke": (10, 10, 10),
        "shadow": (0, 0, 0, 220),
        "badge_bg": (0, 0, 0, 180),
    },
    "fire_red": {
        "name": "Fire Red",
        "fill": (255, 50, 50),
        "stroke": (15, 15, 15),
        "shadow": (0, 0, 0, 230),
        "badge_bg": (0, 0, 0, 190),
    },
    "neon_cyan": {
        "name": "Neon Cyan",
        "fill": (0, 240, 255),
        "stroke": (5, 15, 30),
        "shadow": (0, 0, 0, 220),
        "badge_bg": (5, 15, 30, 180),
    },
    "crisp_white": {
        "name": "Crisp White",
        "fill": (255, 255, 255),
        "stroke": (10, 10, 10),
        "shadow": (0, 0, 0, 230),
        "badge_bg": (0, 0, 0, 180),
    },
}


def is_telugu(text: str) -> bool:
    """Detect if text contains Telugu Unicode characters (\u0c00-\u0c7f)."""
    if not text:
        return False
    return bool(re.search(r"[\u0c00-\u0c7f]", text))


def get_font(is_telugu_text: bool, size: int) -> ImageFont.FreeTypeFont:
    """
    Select the best font for the target script.
    For Telugu or mixed Telugu+English (Tenglish), prioritizes Nirmala UI Bold
    (index=1 in Nirmala.ttc) because it contains full glyph sets for BOTH
    Telugu and Latin/English, preventing missing glyph boxes (tofu) when English
    words (e.g. Barreleye) appear in Telugu titles.
    """
    fonts_dir = os.path.join(utils.root_dir(), "resource", "fonts")

    if is_telugu_text:
        # Nirmala.ttc contains full Telugu + Latin glyphs (crucial for Tenglish titles).
        # NotoSansTelugu is an Indic-only subset and renders English words as boxes.
        candidates = [
            (os.path.join(fonts_dir, "Nirmala.ttc"), 1),
            ("C:\\Windows\\Fonts\\Nirmala.ttc", 1),
            (os.path.join(fonts_dir, "Nirmala.ttc"), 0),
            ("C:\\Windows\\Fonts\\Nirmala.ttc", 0),
            (os.path.join(fonts_dir, "NotoSansTelugu-Bold.ttf"), 0),
            ("C:\\Windows\\Fonts\\gautami.ttf", 0),
            (os.path.join(fonts_dir, "NotoSansTelugu-Regular.ttf"), 0),
        ]
    else:
        candidates = [
            (os.path.join(fonts_dir, "BeVietnamPro-Bold.ttf"), 0),
            ("C:\\Windows\\Fonts\\impact.ttf", 0),
            ("C:\\Windows\\Fonts\\ariblk.ttf", 0),
            (os.path.join(fonts_dir, "Nirmala.ttc"), 1),
            ("C:\\Windows\\Fonts\\Nirmala.ttc", 1),
        ]

    for candidate_path, font_index in candidates:
        if os.path.isfile(candidate_path):
            try:
                return ImageFont.truetype(candidate_path, size, index=font_index)
            except Exception:
                try:
                    return ImageFont.truetype(candidate_path, size)
                except Exception as e:
                    logger.warning(f"Failed to load font candidate {candidate_path}: {e}")

    # Ultimate fallback to default font
    return ImageFont.load_default()


def extract_hook_title_fallback(script: str, is_telugu_lang: bool) -> str:
    """
    Deterministic rule-based fallback hook title when LLM is unavailable.
    Picks a short, dramatic phrase or first line.
    """
    lines = [line.strip() for line in (script or "").splitlines() if line.strip()]
    if not lines:
        return "షాకింగ్ నిజం!" if is_telugu_lang else "MUST WATCH!"

    first_line = lines[0]
    # Strip leading markdown symbols
    first_line = re.sub(r"^[#*\-\s]+", "", first_line)

    if is_telugu_lang:
        # Take first 4-6 Telugu words
        words = first_line.split()
        if len(words) <= 5:
            return " ".join(words)
        return " ".join(words[:4]) + "..."
    else:
        words = first_line.split()
        if len(words) <= 5:
            return " ".join(words).upper()
        return " ".join(words[:4]).upper() + "!"


def generate_hook_title(script: str, language: str = "auto") -> str:
    """
    Generate a punchy 3-5 word high-CTR thumbnail title from the script.
    Tries LLM if configured; otherwise uses intelligent fallback.
    """
    script = (script or "").strip()
    if not script:
        return "షాకింగ్ నిజం!" if language == "te" else "MUST WATCH!"

    is_te = is_telugu(script) if language == "auto" else (language == "te")
    target_lang = "Telugu" if is_te else "English"

    # Attempt to use project LLM service if available
    try:
        from app.services import llm

        system_prompt = (
            "You are a viral YouTube thumbnail copywriter. "
            f"Generate a short, punchy, high-CTR hook title in {target_lang} for this video script. "
            "Constraints:\n"
            "1. Maximum 3 to 5 words only.\n"
            "2. Very catchy, curiosity-inducing, emotional or shocking.\n"
            "3. NO quotation marks, NO explanations, NO hashtags, NO emojis.\n"
            f"4. Must be purely in {target_lang}."
        )
        prompt = f"Video script excerpt:\n{script[:600]}\n\nCatchy Thumbnail Hook Title:"
        response = llm._generate_response(prompt=prompt, system_prompt=system_prompt)
        cleaned = response.strip().strip('"\'“”')
        # Check if output is reasonable length (1 to 8 words)
        if cleaned and len(cleaned.split()) <= 8:
            return cleaned.upper() if not is_te else cleaned
    except Exception as exc:
        logger.debug(f"LLM hook title generation fallback triggered: {exc}")

    return extract_hook_title_fallback(script, is_te)


def craft_flow_prompt(
    script: str,
    aspect_ratio: str = "16:9",
    topic: str = "",
    has_cutout: bool = False,
) -> str:
    """
    Craft an optimized prompt for Google Flow (Nano Banana Pro / Gemini Image).
    Designed to yield cinematic, ultra-detailed thumbnail backgrounds with
    proper composition and negative space.
    """
    # Clean script for summary context
    clean_lines = [l.strip() for l in (script or "").splitlines() if l.strip()]
    context = " ".join(clean_lines[:3]) if clean_lines else (topic or "Exciting storytelling video")

    ratio_note = "16:9 widescreen YouTube thumbnail layout" if aspect_ratio == "16:9" else "9:16 vertical smartphone Shorts / Reels layout"
    space_instruction = (
        "Leave clear negative space on the left side with dramatic ambient lighting for graphic overlay."
        if has_cutout
        else "Dynamic visual storytelling composition with a prominent, expressive hero subject in focus."
    )

    prompt = (
        f"A viral cinematic YouTube thumbnail background for a video about: {context[:250]}. "
        f"{ratio_note}. "
        f"Hyper-detailed, volumetric dramatic rim lighting, vibrant rich saturated colors, high contrast, "
        f"photorealistic 8k quality, depth of field with cinematic blur, trending on ArtStation. "
        f"{space_instruction} "
        f"No watermark, no blurry details, no low quality text."
    )
    return prompt.strip()


def _wrap_text_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Wrap text so each line fits within max_width pixels."""
    words = text.split()
    if not words:
        return [text]

    lines = []
    current_line = words[0]

    for word in words[1:]:
        test_line = f"{current_line} {word}"
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word
    lines.append(current_line)
    return lines


def compose_thumbnail(
    background_image: Union[Image.Image, str, bytes],
    title_text: str,
    cutout_image: Optional[Union[Image.Image, str, bytes]] = None,
    aspect_ratio: str = "16:9",
    style_key: str = "yellow_black",
    cutout_position: str = "right",
    add_badge: bool = True,
) -> Image.Image:
    """
    Master thumbnail composition engine:
    1. Resizes/crops background image to exact aspect ratio (16:9 or 9:16).
    2. Overlays character cutout with alpha transparency and subtle rim-glow/shadow.
    3. Renders bold, multi-line Telugu/English typography with thick contrast strokes.
    """
    target_width, target_height = ASPECT_RATIOS.get(aspect_ratio, (1920, 1080))

    # 1. Load and conform background image
    if isinstance(background_image, (str, Path)):
        bg = Image.open(str(background_image)).convert("RGBA")
    elif isinstance(background_image, bytes):
        bg = Image.open(io.BytesIO(background_image)).convert("RGBA")
    else:
        bg = background_image.convert("RGBA")

    # Crop/fit background to target aspect ratio (cover mode)
    bg_ratio = bg.width / bg.height
    target_ratio = target_width / target_height

    if bg_ratio > target_ratio:
        new_width = int(bg.height * target_ratio)
        offset_x = (bg.width - new_width) // 2
        bg = bg.crop((offset_x, 0, offset_x + new_width, bg.height))
    else:
        new_height = int(bg.width / target_ratio)
        offset_y = (bg.height - new_height) // 2
        bg = bg.crop((0, offset_y, bg.width, offset_y + new_height))

    bg = bg.resize((target_width, target_height), Image.Resampling.LANCZOS)

    # 2. Overlay Character Cutout if provided
    if cutout_image:
        try:
            if isinstance(cutout_image, (str, Path)):
                cutout = Image.open(str(cutout_image)).convert("RGBA")
            elif isinstance(cutout_image, bytes):
                cutout = Image.open(io.BytesIO(cutout_image)).convert("RGBA")
            else:
                cutout = cutout_image.convert("RGBA")

            # Determine cutout scaling (e.g. ~85% of thumbnail height in 16:9, ~55% in 9:16)
            scale_target = 0.88 if aspect_ratio == "16:9" else 0.60
            scale_height = int(target_height * scale_target)
            cutout_ratio = cutout.width / cutout.height
            scale_width = int(scale_height * cutout_ratio)

            # Cap width so cutout doesn't dominate more than 55% of canvas width
            if scale_width > int(target_width * 0.55):
                scale_width = int(target_width * 0.55)
                scale_height = int(scale_width / cutout_ratio)

            cutout_resized = cutout.resize((scale_width, scale_height), Image.Resampling.LANCZOS)

            # Position
            if cutout_position == "left":
                pos_x = int(target_width * 0.03)
                pos_y = target_height - scale_height
            elif cutout_position == "center":
                pos_x = (target_width - scale_width) // 2
                pos_y = target_height - scale_height
            else:  # right
                pos_x = target_width - scale_width - int(target_width * 0.03)
                pos_y = target_height - scale_height

            # Create subtle rim shadow for cutout to blend smoothly
            alpha = cutout_resized.getchannel("A")
            shadow_mask = alpha.filter(ImageFilter.GaussianBlur(radius=15))
            shadow_layer = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
            shadow_color = Image.new("RGBA", (scale_width, scale_height), (0, 0, 0, 160))
            shadow_layer.paste(shadow_color, (pos_x + 5, pos_y + 10), shadow_mask)

            bg = Image.alpha_composite(bg, shadow_layer)
            bg.paste(cutout_resized, (pos_x, pos_y), cutout_resized)
        except Exception as e:
            logger.warning(f"Failed to overlay character cutout: {e}")

    # 3. Typography Compositor
    title_text = (title_text or "").strip()
    if title_text:
        style = STYLE_PRESETS.get(style_key, STYLE_PRESETS["yellow_black"])
        is_te = is_telugu(title_text)

        # Determine typography bounding zone
        if aspect_ratio == "16:9":
            # If cutout is on the right, place text on the left
            if cutout_image and cutout_position == "right":
                max_text_width = int(target_width * 0.48)
                start_x = int(target_width * 0.05)
            elif cutout_image and cutout_position == "left":
                max_text_width = int(target_width * 0.48)
                start_x = int(target_width * 0.47)
            else:
                max_text_width = int(target_width * 0.70)
                start_x = int(target_width * 0.06)
            start_y = int(target_height * 0.12)
            base_font_size = 110 if len(title_text) <= 15 else 90
        else:  # 9:16 vertical
            max_text_width = int(target_width * 0.88)
            start_x = int(target_width * 0.06)
            start_y = int(target_height * 0.08)
            base_font_size = 100 if len(title_text) <= 15 else 80

        # Create temporary draw to measure text
        temp_img = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
        temp_draw = ImageDraw.Draw(temp_img)

        # Adaptive font sizing to avoid over-wrapping
        font = get_font(is_te, base_font_size)
        lines = _wrap_text_lines(temp_draw, title_text, font, max_text_width)

        if len(lines) > 3:
            base_font_size = int(base_font_size * 0.75)
            font = get_font(is_te, base_font_size)
            lines = _wrap_text_lines(temp_draw, title_text, font, max_text_width)

        # Line height calculation
        line_height = int(base_font_size * 1.25)
        stroke_width = max(6, int(base_font_size * 0.09))
        shadow_offset = max(4, int(base_font_size * 0.06))

        # Optional badge/pill background behind text for guaranteed contrast
        if add_badge:
            total_text_h = len(lines) * line_height
            badge_pad = 24
            badge_layer = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
            badge_draw = ImageDraw.Draw(badge_layer)

            for idx, line in enumerate(lines):
                line_bbox = temp_draw.textbbox((start_x, start_y + idx * line_height), line, font=font)
                badge_box = (
                    line_bbox[0] - badge_pad,
                    line_bbox[1] - badge_pad // 2,
                    line_bbox[2] + badge_pad,
                    line_bbox[3] + badge_pad // 2,
                )
                badge_draw.rounded_rectangle(badge_box, radius=16, fill=style["badge_bg"])

            bg = Image.alpha_composite(bg, badge_layer)

        # Draw text layers: Shadow -> Stroke -> Fill
        text_layer = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
        text_draw = ImageDraw.Draw(text_layer)

        for idx, line in enumerate(lines):
            pos = (start_x, start_y + idx * line_height)
            # 1. Drop Shadow
            text_draw.text(
                (pos[0] + shadow_offset, pos[1] + shadow_offset),
                line,
                font=font,
                fill=style["shadow"],
                stroke_width=stroke_width,
                stroke_fill=style["shadow"],
            )
            # 2. Main Stroke and Fill
            text_draw.text(
                pos,
                line,
                font=font,
                fill=style["fill"],
                stroke_width=stroke_width,
                stroke_fill=style["stroke"],
            )

        bg = Image.alpha_composite(bg, text_layer)

    return bg.convert("RGB")
