"""Plan Flow prompts per style, then finish the images the user brings back.

Images are generated in Google Flow by the user (free with a Gemini plan).
This module never calls an image API: it writes the prompts, and turns each
uploaded Flow image into an upload-ready thumbnail version.
"""

import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence

from app.services import thumbnail
from app.services.thumbnail_ai import postprocess, prompts
from app.services.thumbnail_ai.brief import Concept, default_concepts
from app.utils import utils


@dataclass
class ThumbnailVersion:
    image_bytes: bytes
    file_ext: str
    file_path: str
    # The image exactly as downloaded from Flow. In overlay mode this has no
    # text, so it is the one to attach when editing in Flow again.
    source_bytes: bytes = b""
    source_hash: str = ""
    instruction: str = ""


@dataclass
class ThumbnailVariant:
    style_key: str
    label: str
    prompt: str
    concept: Optional[Concept] = None
    aspect_ratio: str = "16:9"
    has_reference: bool = False
    # True: Flow leaves the text area empty and the app draws the text.
    overlay_text: bool = False
    text_style: str = "yellow_black"
    badge_text: str = ""
    output_dir: str = ""
    versions: list = field(default_factory=list)

    @property
    def current(self) -> Optional[ThumbnailVersion]:
        return self.versions[-1] if self.versions else None

    @property
    def text_lines(self) -> tuple:
        concept = self.concept
        return (concept.headline, concept.subtitle, self.badge_text) if concept else ()


def plan_variants(
    script: str,
    title: str = "",
    aspect_ratio: str = "16:9",
    subtitle: str = "",
    badge_text: str = "",
    style_keys: Optional[Sequence[str]] = None,
    concepts: Optional[Sequence[Concept]] = None,
    has_reference: bool = False,
    overlay_text: bool = False,
    text_style: str = "yellow_black",
) -> list[ThumbnailVariant]:
    """Build one ready-to-paste Flow prompt per concept (or per style)."""
    if concepts:
        plan = [c for c in concepts if c.style_key in prompts.STYLE_VARIANTS]
    else:
        keys = [k for k in (style_keys or prompts.DEFAULT_STYLE_KEYS) if k in prompts.STYLE_VARIANTS]
        plan = default_concepts(title, subtitle, keys or prompts.DEFAULT_STYLE_KEYS)
    topic = prompts.summarize_topic(script) or title
    output_dir = utils.storage_dir(os.path.join("thumbnails", time.strftime("%Y%m%d-%H%M%S")))

    variants = []
    for concept in plan:
        variants.append(
            ThumbnailVariant(
                style_key=concept.style_key,
                label=prompts.STYLE_VARIANTS[concept.style_key]["label"],
                prompt=prompts.build_prompt(
                    topic=topic,
                    title=concept.headline,
                    aspect_ratio=aspect_ratio,
                    style_key=concept.style_key,
                    subtitle=concept.subtitle,
                    badge_text=badge_text.strip(),
                    has_reference=has_reference,
                    render_text=not overlay_text,
                    scene=concept.scene,
                ),
                concept=concept,
                aspect_ratio=aspect_ratio,
                has_reference=has_reference,
                overlay_text=overlay_text,
                text_style=text_style,
                badge_text=badge_text.strip(),
                output_dir=output_dir,
            )
        )
    return variants


def edit_prompt(variant: ThumbnailVariant, instruction: str) -> str:
    """Flow prompt that applies a plain-language change to the current image."""
    instruction = (instruction or "").strip()
    if not instruction:
        raise ValueError("edit instruction is empty")
    return prompts.build_edit_prompt(
        instruction,
        aspect_ratio=variant.aspect_ratio,
        has_reference=variant.has_reference,
        text_lines=variant.text_lines,
        render_text=not variant.overlay_text,
    )


def add_flow_image(
    variant: ThumbnailVariant, image_bytes: bytes, instruction: str = ""
) -> Optional[ThumbnailVersion]:
    """Finish an image downloaded from Flow and append it as a new version.

    Fits it to YouTube's exact size, draws the text in overlay mode, keeps it
    under the 2 MB upload limit and saves it. Returns None when this exact file
    was already added (Streamlit reruns re-deliver the same upload).
    """
    source_hash = hashlib.sha256(image_bytes).hexdigest()
    if any(v.source_hash == source_hash for v in variant.versions):
        return None

    image_source = image_bytes
    if variant.overlay_text and variant.concept:
        image_source = thumbnail.compose_thumbnail(
            background_image=image_bytes,
            title_text="\n".join(t for t in (variant.concept.headline, variant.concept.subtitle) if t),
            aspect_ratio=variant.aspect_ratio,
            style_key=variant.text_style,
        )
    image = postprocess.fit_canvas(image_source, variant.aspect_ratio)
    data, ext = postprocess.encode_for_upload(image)

    os.makedirs(variant.output_dir, exist_ok=True)
    path = os.path.join(variant.output_dir, f"{variant.style_key}_v{len(variant.versions) + 1}.{ext}")
    with open(path, "wb") as f:
        f.write(data)
    version = ThumbnailVersion(
        image_bytes=data,
        file_ext=ext,
        file_path=path,
        source_bytes=image_bytes,
        source_hash=source_hash,
        instruction=instruction.strip(),
    )
    variant.versions.append(version)
    return version
