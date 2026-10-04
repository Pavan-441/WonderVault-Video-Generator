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
    Picks a short, dramatic phrase or complete clause instead of trailing truncation.
    """
    lines = [line.strip() for line in (script or "").splitlines() if line.strip()]
    if not lines:
        return "షాకింగ్ నిజం!" if is_telugu_lang else "MUST WATCH!"

    first_line = re.sub(r"^[#*\-\s]+", "", lines[0])

    if is_telugu_lang:
        lower_script = script.lower()
        if "శని" in script or "saturn" in lower_script:
            return "శని గ్రహం అద్భుతం!"
        if "నిజం" in script or "రహస్యం" in script:
            return "షాకింగ్ రహస్యం!"
        if "అద్భుత" in script:
            return "కనులవిందు అద్భుతం!"
        # Fallback to first clause without trailing ellipsis
        clause = re.split(r"[,.!?।;]", first_line)[0].strip()
        words = clause.split()
        if len(words) <= 5:
            return " ".join(words)
        return " ".join(words[:4])
    else:
        clause = re.split(r"[,.!?।;]", first_line)[0].strip()
        words = clause.split()
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

        prompt = (
            "You are a viral YouTube thumbnail copywriter.\n"
            f"Generate a short, punchy, high-CTR hook title in {target_lang} for this video script.\n"
            "Constraints:\n"
            "1. Maximum 3 to 5 words only.\n"
            "2. Very catchy, curiosity-inducing, emotional or shocking.\n"
            "3. NO quotation marks, NO explanations, NO hashtags, NO emojis, NO trailing punctuation.\n"
            f"4. Must be purely in {target_lang} (or common {target_lang} transliteration).\n\n"
            f"Video script excerpt:\n{script[:800]}\n\n"
            "Catchy Thumbnail Hook Title:"
        )
        response = llm._generate_response(prompt=prompt)
        cleaned = response.strip().strip('"\'“”`*#').rstrip(".!?:")
        # Check if output is reasonable length (1 to 8 words)
        if cleaned and len(cleaned.split()) <= 8:
            return cleaned.upper() if not is_te else cleaned
    except Exception as exc:
        logger.warning(f"LLM hook title generation fallback triggered: {exc}")

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


def _to_argb(c: Tuple) -> int:
    a = c[3] if len(c) > 3 else 255
    return ((a & 0xFF) << 24) | ((c[0] & 0xFF) << 16) | ((c[1] & 0xFF) << 8) | (c[2] & 0xFF)


def _render_text_layer_gdiplus(
    canvas_w: int,
    canvas_h: int,
    lines: list[str],
    line_positions: list[Tuple[float, float]],
    font_family_name: str,
    font_size: float,
    fill_color: Tuple,
    stroke_color: Tuple,
    stroke_width: float,
    shadow_color: Tuple,
    shadow_offset: Tuple[float, float],
    badge_bg: Tuple,
    add_badge: bool = True,
) -> Optional[Image.Image]:
    """
    Renders complex Indic/Telugu and multi-script typography using Windows native GDI+
    (gdiplus.dll). GDI+ has built-in OpenType GSUB/GPOS complex shaping (Uniscribe engine),
    ensuring accurate conjuncts, ligatures, vothulu (subscripts), and vowel signs.
    """
    if os.name != "nt":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        gdiplus = ctypes.windll.gdiplus
    except Exception as e:
        logger.debug(f"GDI+ library unavailable: {e}")
        return None

    class GdiplusStartupInput(ctypes.Structure):
        _fields_ = [("v", wintypes.UINT), ("cb", ctypes.c_void_p), ("t", wintypes.BOOL), ("c", wintypes.BOOL)]

    token = ctypes.c_ulong()
    status = gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(GdiplusStartupInput(1, None, False, False)), None)
    if status != 0:
        return None

    p_bitmap = ctypes.c_void_p()
    p_graphics = ctypes.c_void_p()
    p_family = ctypes.c_void_p()
    p_shadow_pen = ctypes.c_void_p()
    p_shadow_brush = ctypes.c_void_p()
    p_stroke_pen = ctypes.c_void_p()
    p_fill_brush = ctypes.c_void_p()
    paths = []
    line_boxes = []

    class RectF(ctypes.Structure):
        _fields_ = [("X", ctypes.c_float), ("Y", ctypes.c_float), ("W", ctypes.c_float), ("H", ctypes.c_float)]

    try:
        # 0x26200A = PixelFormat32bppARGB
        if gdiplus.GdipCreateBitmapFromScan0(canvas_w, canvas_h, 0, 0x26200A, None, ctypes.byref(p_bitmap)) != 0:
            return None
        gdiplus.GdipGetImageGraphicsContext(p_bitmap, ctypes.byref(p_graphics))
        gdiplus.GdipSetSmoothingMode(p_graphics, 4)  # AntiAlias
        gdiplus.GdipSetTextRenderingHint(p_graphics, 4)  # AntiAliasGridFit

        # Font family with fallback
        res = gdiplus.GdipCreateFontFamilyFromName(font_family_name, None, ctypes.byref(p_family))
        if res != 0:
            res = gdiplus.GdipCreateFontFamilyFromName("Nirmala UI", None, ctypes.byref(p_family))
        if res != 0:
            gdiplus.GdipCreateFontFamilyFromName("Arial", None, ctypes.byref(p_family))

        # Create Pens and Brushes
        gdiplus.GdipCreatePen1(ctypes.c_uint(_to_argb(shadow_color)), ctypes.c_float(stroke_width + 4.0), 2, ctypes.byref(p_shadow_pen))
        gdiplus.GdipCreateSolidFill(ctypes.c_uint(_to_argb(shadow_color)), ctypes.byref(p_shadow_brush))
        gdiplus.GdipCreatePen1(ctypes.c_uint(_to_argb(stroke_color)), ctypes.c_float(stroke_width), 2, ctypes.byref(p_stroke_pen))
        gdiplus.GdipCreateSolidFill(ctypes.c_uint(_to_argb(fill_color)), ctypes.byref(p_fill_brush))

        # Construct vector paths for each line
        for line, (x, y) in zip(lines, line_positions):
            if not line.strip():
                continue
            p_path = ctypes.c_void_p()
            gdiplus.GdipCreatePath(0, ctypes.byref(p_path))
            layout_rect = RectF(float(x), float(y), float(canvas_w - x), float(font_size * 2.5))
            # FontStyleBold = 1
            gdiplus.GdipAddPathString(p_path, line, -1, p_family, 1, ctypes.c_float(font_size), ctypes.byref(layout_rect), None)
            paths.append(p_path)

            bounds = RectF()
            gdiplus.GdipGetPathWorldBounds(p_path, ctypes.byref(bounds), None, None)
            line_boxes.append((bounds.X, bounds.Y, bounds.X + bounds.W, bounds.Y + bounds.H))

        if not paths:
            return None

        # 1. Drop Shadow Pass
        dx, dy = shadow_offset
        gdiplus.GdipTranslateWorldTransform(p_graphics, ctypes.c_float(dx), ctypes.c_float(dy), 0)
        for p_path in paths:
            gdiplus.GdipDrawPath(p_graphics, p_shadow_pen, p_path)
            gdiplus.GdipFillPath(p_graphics, p_shadow_brush, p_path)
        gdiplus.GdipResetWorldTransform(p_graphics)

        # 2. Main Stroke and Fill Pass
        for p_path in paths:
            gdiplus.GdipDrawPath(p_graphics, p_stroke_pen, p_path)
            gdiplus.GdipFillPath(p_graphics, p_fill_brush, p_path)

        # Lock bits and transfer to PIL Image
        class BitmapData(ctypes.Structure):
            _fields_ = [("W", wintypes.UINT), ("H", wintypes.UINT), ("S", ctypes.c_int), ("P", ctypes.c_int), ("Scan0", ctypes.c_void_p), ("R", ctypes.c_void_p)]

        class Rect(ctypes.Structure):
            _fields_ = [("X", ctypes.c_int), ("Y", ctypes.c_int), ("W", ctypes.c_int), ("H", ctypes.c_int)]

        bmp_data = BitmapData()
        gdiplus.GdipBitmapLockBits(p_bitmap, ctypes.byref(Rect(0, 0, canvas_w, canvas_h)), 1, 0x26200A, ctypes.byref(bmp_data))
        raw_bytes = (ctypes.c_char * (bmp_data.S * canvas_h)).from_address(bmp_data.Scan0)
        text_img = Image.frombuffer("RGBA", (canvas_w, canvas_h), bytes(raw_bytes), "raw", "BGRA", bmp_data.S, 1).copy()
        gdiplus.GdipBitmapUnlockBits(p_bitmap, ctypes.byref(bmp_data))

    except Exception as exc:
        logger.warning(f"GDI+ text rendering error: {exc}")
        return None
    finally:
        for p in paths:
            try:
                gdiplus.GdipDeletePath(p)
            except Exception:
                pass
        if p_shadow_pen:
            try:
                gdiplus.GdipDeletePen(p_shadow_pen)
            except Exception:
                pass
        if p_shadow_brush:
            try:
                gdiplus.GdipDeleteBrush(p_shadow_brush)
            except Exception:
                pass
        if p_stroke_pen:
            try:
                gdiplus.GdipDeletePen(p_stroke_pen)
            except Exception:
                pass
        if p_fill_brush:
            try:
                gdiplus.GdipDeleteBrush(p_fill_brush)
            except Exception:
                pass
        if p_family:
            try:
                gdiplus.GdipDeleteFontFamily(p_family)
            except Exception:
                pass
        if p_graphics:
            try:
                gdiplus.GdipDeleteGraphics(p_graphics)
            except Exception:
                pass
        if p_bitmap:
            try:
                gdiplus.GdipDisposeImage(p_bitmap)
            except Exception:
                pass
        try:
            gdiplus.GdiplusShutdown(token)
        except Exception:
            pass

    # Optional Pill Badge layer
    composite_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    if add_badge and line_boxes:
        badge_pad_x = 24
        badge_pad_y = 12
        badge_draw = ImageDraw.Draw(composite_layer)
        for box in line_boxes:
            b = (
                int(box[0] - badge_pad_x),
                int(box[1] - badge_pad_y),
                int(box[2] + badge_pad_x),
                int(box[3] + badge_pad_y),
            )
            badge_draw.rounded_rectangle(b, radius=16, fill=badge_bg)

    return Image.alpha_composite(composite_layer, text_img)


def _wrap_text_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Wrap text so each line fits within max_width pixels, respecting explicit newlines."""
    paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
    if not paragraphs:
        return [text.strip()] if text.strip() else []

    all_lines = []
    for para in paragraphs:
        words = para.split()
        if not words:
            continue
        current_line = words[0]
        for word in words[1:]:
            test_line = f"{current_line} {word}"
            bbox = draw.textbbox((0, 0), test_line, font=font)
            if (bbox[2] - bbox[0]) <= max_width:
                current_line = test_line
            else:
                all_lines.append(current_line)
                current_line = word
        all_lines.append(current_line)
    return all_lines


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

        # Attempt native Windows GDI+ rendering (supports full complex Indic/Telugu shaping)
        gdiplus_layer = None
        if os.name == "nt":
            try:
                family_name = "Nirmala UI" if (is_te or not style.get("font_family")) else style.get("font_family", "Nirmala UI")
                line_positions = [(float(start_x), float(start_y + idx * line_height)) for idx in range(len(lines))]
                gdiplus_layer = _render_text_layer_gdiplus(
                    canvas_w=target_width,
                    canvas_h=target_height,
                    lines=lines,
                    line_positions=line_positions,
                    font_family_name=family_name,
                    font_size=float(base_font_size),
                    fill_color=style["fill"],
                    stroke_color=style["stroke"],
                    stroke_width=float(stroke_width),
                    shadow_color=style["shadow"],
                    shadow_offset=(float(shadow_offset), float(shadow_offset + 2)),
                    badge_bg=style["badge_bg"],
                    add_badge=add_badge,
                )
            except Exception as e:
                logger.warning(f"GDI+ thumbnail text rendering failed, falling back to PIL: {e}")
                gdiplus_layer = None

        if gdiplus_layer is not None:
            bg = Image.alpha_composite(bg, gdiplus_layer)
        else:
            # Fallback PIL renderer for non-Windows platforms or if GDI+ is unavailable
            if add_badge:
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
