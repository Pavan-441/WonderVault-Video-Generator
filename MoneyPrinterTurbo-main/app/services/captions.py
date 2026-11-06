"""Shorts-style captions: a few words at a time, the spoken word highlighted.

Timing comes from the sentence subtitles (already matched to the voiceover). Inside a
sentence, each word gets a share of the time by its length. When Whisper word timings
are available, they bend that split to the real speech, using character positions so
the script's spelling never has to match Whisper's.
"""

import json
import os
import unicodedata
from dataclasses import dataclass

import numpy as np
from loguru import logger
from PIL import Image, ImageDraw, ImageFont, features

DEFAULT_MAX_WORDS = 3
DEFAULT_HIGHLIGHT = "#FFE600"
MAX_LINES = 2


@dataclass(frozen=True)
class CaptionFrame:
    start: float
    end: float
    words: tuple[str, ...]
    active: int


def words_path(subtitle_path: str) -> str:
    return f"{os.path.splitext(subtitle_path)[0]}.words.json"


def save_word_timeline(path: str, words: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as file:
        json.dump(words, file, ensure_ascii=False)


def load_word_timeline(subtitle_path: str) -> list[dict]:
    try:
        with open(words_path(subtitle_path), encoding="utf-8") as file:
            words = json.load(file)
        return [w for w in words if isinstance(w, dict) and w.get("end", 0) > w.get("start", 0)]
    except (OSError, ValueError):
        return []


def _letters(text: str) -> int:
    return sum(1 for ch in text if unicodedata.category(ch)[0] in "LNM")


def word_starts(words: list[str], start: float, end: float, timeline: list[dict] | None = None) -> list[float]:
    """Start time of each word in a line spoken between start and end."""
    weights = [max(_letters(w), 1) for w in words]
    total = float(sum(weights))
    fractions = np.cumsum([0.0] + weights[:-1]) / total

    # Anchor points (fraction of the line's letters -> time) from Whisper words.
    xs, ys = [0.0], [start]
    spoken = [w for w in timeline or [] if start - 0.05 <= w["start"] < end]
    spoken_total = float(sum(max(_letters(w.get("word", "")), 1) for w in spoken))
    position = 0.0
    for item in spoken:
        fraction = position / spoken_total if spoken_total else 0.0
        if fraction > xs[-1] and item["start"] > ys[-1]:
            xs.append(fraction)
            ys.append(item["start"])
        position += max(_letters(item.get("word", "")), 1)
    xs.append(1.0)
    ys.append(max(end, ys[-1]))
    return [float(t) for t in np.interp(fractions, xs, ys)]


def chunk_words(words: list[str], max_words: int, fits) -> list[list[int]]:
    """Groups word indexes into captions of at most max_words that fit the screen."""
    chunks, current = [], []
    for index, word in enumerate(words):
        candidate = current + [index]
        if current and (len(candidate) > max_words or not fits([words[i] for i in candidate])):
            chunks.append(current)
            candidate = [index]
        current = candidate
    if current:
        chunks.append(current)
    return chunks


def build_frames(subtitle_items, max_words: int, fits, timeline=None) -> list[CaptionFrame]:
    """subtitle_items: [((start, end), text), ...] from the sentence subtitles."""
    frames = []
    max_words = max(1, int(max_words or DEFAULT_MAX_WORDS))
    for (start, end), text in subtitle_items:
        words = str(text or "").split()
        if not words or end <= start:
            continue
        starts = word_starts(words, float(start), float(end), timeline)
        ends = starts[1:] + [float(end)]
        for chunk in chunk_words(words, max_words, fits):
            chunk_words_tuple = tuple(words[i] for i in chunk)
            for position, index in enumerate(chunk):
                # The last word of a caption stays until the caption's end.
                frame_end = ends[chunk[-1]] if position == len(chunk) - 1 else ends[index]
                if frame_end > starts[index]:
                    frames.append(CaptionFrame(starts[index], frame_end, chunk_words_tuple, position))
    return frames


class CaptionRenderer:
    """Draws one caption image (RGBA) with the active word in the highlight colour."""

    def __init__(self, font_path, font_size, text_color, highlight_color, stroke_color, stroke_width, max_width):
        layout = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
        self.font = ImageFont.truetype(font_path, int(font_size), layout_engine=layout)
        self.text_color = text_color or "#FFFFFF"
        self.highlight_color = highlight_color or DEFAULT_HIGHLIGHT
        self.stroke_color = stroke_color or "#000000"
        # Shorts captions need a visible outline even when the sentence style has none.
        self.stroke_width = max(int(round(stroke_width or 0)), max(2, int(font_size) // 18))
        self.max_width = int(max_width)
        self.space = self.font.getlength(" ")
        ascent, descent = self.font.getmetrics()
        # Telugu vowel signs reach above the ascent and below the descent.
        self.pad = int(font_size * 0.35) + self.stroke_width
        self.line_height = ascent + descent + int(font_size * 0.2)

    def _width(self, words) -> float:
        return sum(self.font.getlength(w) for w in words) + self.space * (len(words) - 1)

    def _lines(self, words) -> list[list[int]]:
        lines, current = [], []
        for index, word in enumerate(words):
            if current and self._width([words[i] for i in current + [index]]) > self.max_width:
                lines.append(current)
                current = []
            current.append(index)
        if current:
            lines.append(current)
        return lines

    def fits(self, words) -> bool:
        return len(self._lines(words)) <= MAX_LINES

    def render(self, words, active: int) -> np.ndarray:
        lines = self._lines(list(words))
        height = len(lines) * self.line_height + 2 * self.pad
        width = self.max_width + 2 * self.pad
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for row, line in enumerate(lines):
            line_width = self._width([words[i] for i in line])
            x = self.pad + (self.max_width - line_width) / 2
            y = self.pad + row * self.line_height
            for index in line:
                draw.text(
                    (x, y),
                    words[index],
                    font=self.font,
                    fill=self.highlight_color if index == active else self.text_color,
                    stroke_width=self.stroke_width,
                    stroke_fill=self.stroke_color,
                )
                x += self.font.getlength(words[index]) + self.space
        return np.array(image)


def transcribe_word_timeline(audio_file: str) -> list[dict]:
    """Word timings from Whisper, or [] when Whisper is unavailable."""
    from app.services import subtitle

    model = subtitle.get_model()
    if model is None:
        return []
    try:
        segments, _info = model.transcribe(audio_file, beam_size=5, word_timestamps=True)
        return [
            {"word": w.word.strip(), "start": float(w.start), "end": float(w.end)}
            for segment in segments
            for w in (segment.words or [])
            if w.word.strip()
        ]
    except Exception as exc:
        logger.warning(f"Whisper word timing failed, captions use estimated timing: {exc}")
        return []
