"""Reads the ready-made script libraries (*_WONDERVAULT.md) in the repo root.

Each video entry looks like:

    ### Video #001 — Title            (or "### వీడియో #001 — ...")
    | **Video Subject** | `...` |
    | **Video Keywords** ... | `a, b, c` |
    | **Thumbnail Title** | `...` |
    #### 🎙️ ... script heading
    ```text
    the narration
    ```
    #### 📤 Upload Kit
    ...
"""

import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.services import bilingual
from app.utils import utils

LIBRARY_GLOB = "*WONDERVAULT*.md"

_ENTRY = re.compile(r"^### (?:Video|వీడియో) #(\d+)\s*[—–-]\s*(.+?)\s*$", re.MULTILINE)
_SECTION_END = re.compile(r"^#{1,3} ", re.MULTILINE)
_TABLE_ROW = re.compile(r"^\|\s*\*\*(.+?)\*\*[^|]*\|\s*`(.*?)`\s*\|\s*$", re.MULTILINE)
_SCRIPT_BLOCK = re.compile(r"^#### 🎙️.*?\n```text\n(.*?)\n```", re.MULTILINE | re.DOTALL)
_UPLOAD_KIT = re.compile(r"^#### 📤 Upload Kit\s*\n(.*)", re.MULTILINE | re.DOTALL)
_TRAILER = re.compile(r"\n(?:---|> 📌).*", re.DOTALL)


@dataclass(frozen=True)
class LibraryScript:
    library: str
    number: int
    title: str
    subject: str
    script: str
    keywords: str = ""
    thumbnail_title: str = ""
    upload_kit: str = ""

    @property
    def id(self) -> str:
        return f"{self.library}#{self.number:03d}"

    @property
    def language(self) -> str:
        return bilingual.detect(self.script)


def library_dirs() -> list[Path]:
    """The repo root (one level above the app) and the app folder itself."""
    app_root = Path(utils.root_dir())
    return [app_root.parent, app_root]


def find_libraries() -> list[Path]:
    found: dict[str, Path] = {}
    for directory in library_dirs():
        if directory.is_dir():
            for path in sorted(directory.glob(LIBRARY_GLOB)):
                found.setdefault(path.name, path)
    return list(found.values())


def library_name(path: Path) -> str:
    return path.stem


def _entry_fields(block: str) -> dict:
    return {label.strip().lower(): value.strip() for label, value in _TABLE_ROW.findall(block)}


def parse_library(text: str, library: str) -> list[LibraryScript]:
    entries = []
    matches = list(_ENTRY.finditer(text))
    for index, match in enumerate(matches):
        start = match.end()
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:stop]
        # A "## Section" heading ends the entry too ("####" sub-headings do not).
        section_break = _SECTION_END.search(block)
        if section_break:
            block = block[: section_break.start()]
        script_match = _SCRIPT_BLOCK.search(block)
        if not script_match:
            continue
        fields = _entry_fields(block)
        kit_match = _UPLOAD_KIT.search(block)
        upload_kit = _TRAILER.sub("", kit_match.group(1)).strip() if kit_match else ""
        title = match.group(2).strip()
        entries.append(
            LibraryScript(
                library=library,
                number=int(match.group(1)),
                title=title,
                subject=fields.get("video subject", "") or title,
                script=script_match.group(1).strip(),
                keywords=next((v for k, v in fields.items() if k.startswith("video keywords")), ""),
                thumbnail_title=fields.get("thumbnail title", ""),
                upload_kit=upload_kit,
            )
        )
    return sorted(entries, key=lambda e: e.number)


def load_all() -> list[LibraryScript]:
    scripts = []
    for path in find_libraries():
        try:
            scripts += parse_library(path.read_text(encoding="utf-8"), library_name(path))
        except OSError as exc:
            logger.warning(f"cannot read script library {path}: {exc}")
    return scripts


def safe_folder_name(text: str, limit: int = 60) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", text or "")
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned[:limit].rstrip(" .") or "video"

