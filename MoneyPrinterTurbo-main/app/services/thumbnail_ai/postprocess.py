"""Turn raw model output into an upload-ready thumbnail file."""

import io
from typing import Union

from PIL import Image, ImageFilter

# YouTube's recommended thumbnail size is 1280x720; Shorts covers are 1080x1920.
OUTPUT_SIZES = {"16:9": (1280, 720), "9:16": (1080, 1920)}

# YouTube rejects custom thumbnails larger than 2 MB.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024


def _load(image: Union[Image.Image, bytes]) -> Image.Image:
    if isinstance(image, Image.Image):
        return image.convert("RGB")
    return Image.open(io.BytesIO(image)).convert("RGB")


def fit_canvas(image: Union[Image.Image, bytes], aspect_ratio: str = "16:9") -> Image.Image:
    """Center-crop to the target aspect ratio and resize to the exact canvas."""
    img = _load(image)
    target_w, target_h = OUTPUT_SIZES.get(aspect_ratio, OUTPUT_SIZES["16:9"])
    target_ratio = target_w / target_h
    ratio = img.width / img.height
    if ratio > target_ratio:
        new_w = round(img.height * target_ratio)
        left = (img.width - new_w) // 2
        img = img.crop((left, 0, left + new_w, img.height))
    elif ratio < target_ratio:
        new_h = round(img.width / target_ratio)
        top = (img.height - new_h) // 2
        img = img.crop((0, top, img.width, top + new_h))
    img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
    # A light sharpen keeps edges crisp after downscaling.
    return img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))


def encode_for_upload(image: Image.Image) -> tuple[bytes, str]:
    """Encode as PNG when it fits under YouTube's limit, otherwise as JPEG."""
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    if buf.tell() <= MAX_UPLOAD_BYTES:
        return buf.getvalue(), "png"
    for quality in (95, 90, 85, 80, 75, 70):
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
        if buf.tell() <= MAX_UPLOAD_BYTES:
            break
    return buf.getvalue(), "jpg"
