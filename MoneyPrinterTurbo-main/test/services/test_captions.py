"""Shorts-style word-highlight captions."""

import os
from unittest.mock import patch

import app  # noqa: F401  isort: skip  (puts the bundled FriBiDi DLL on PATH first)
import numpy as np
import pytest

from app.models.schema import VideoParams
from app.services import captions, task, voice
from app.services import video as vd
from app.utils import utils

TENGLISH = "Black Hole లోకి కాళ్ళతో పడితే, మీ బాడీ నూడుల్స్ లా సాగిపోతుంది!"


def always_fits(_words):
    return True


def test_word_times_follow_word_length():
    starts = captions.word_starts(["a", "bbb"], 10.0, 14.0)
    assert starts == [10.0, 11.0]


def test_whisper_words_bend_the_timing_to_real_speech():
    timeline = [
        {"word": "a", "start": 10.0, "end": 10.2},
        {"word": "bbb", "start": 13.0, "end": 13.8},
    ]
    starts = captions.word_starts(["a", "bbb"], 10.0, 14.0, timeline)
    assert starts == [10.0, pytest.approx(13.0)]


def test_whisper_words_outside_the_line_are_ignored():
    timeline = [{"word": "zzz", "start": 30.0, "end": 31.0}]
    assert captions.word_starts(["a", "bbb"], 10.0, 14.0, timeline) == [10.0, 11.0]


def test_chunks_respect_word_count_and_screen_width():
    words = ["one", "two", "three", "four", "five"]
    assert captions.chunk_words(words, 2, always_fits) == [[0, 1], [2, 3], [4]]
    narrow = lambda chunk: sum(len(w) for w in chunk) <= 8  # noqa: E731
    assert captions.chunk_words(words, 5, narrow) == [[0, 1], [2], [3, 4]]


def test_frames_highlight_each_word_and_hold_the_last_one():
    frames = captions.build_frames([((0.0, 6.0), "aa bb cc dd")], 3, always_fits)
    assert [(f.words, f.active) for f in frames] == [
        (("aa", "bb", "cc"), 0),
        (("aa", "bb", "cc"), 1),
        (("aa", "bb", "cc"), 2),
        (("dd",), 0),
    ]
    assert [(f.start, f.end) for f in frames] == [(0.0, 1.5), (1.5, 3.0), (3.0, 4.5), (4.5, 6.0)]


def test_empty_or_zero_length_lines_are_skipped():
    assert captions.build_frames([((1.0, 1.0), "a b"), ((2.0, 3.0), "  ")], 3, always_fits) == []


def _renderer(highlight="#FFE600"):
    font = vd.resolve_subtitle_font(os.path.join(utils.font_dir(), "NotoSansTelugu-Bold.ttf"), TENGLISH)
    return captions.CaptionRenderer(font, 70, "#FFFFFF", highlight, "#000000", 0, 900)


def _count(image, rgb):
    return int(np.all(image[:, :, :3] == rgb, axis=2).sum())


def test_renderer_colours_only_the_active_word():
    renderer = _renderer()
    words = ("బాడీ", "నూడుల్స్", "లా")
    first = renderer.render(words, 0)
    second = renderer.render(words, 1)
    assert first.shape[2] == 4 and first.shape[1] == 900 + 2 * renderer.pad
    assert _count(first, (255, 230, 0)) > 0 and _count(second, (255, 230, 0)) > _count(first, (255, 230, 0))
    # A visible outline even when the sentence style has none.
    assert renderer.stroke_width >= 3


def test_renderer_wraps_to_two_lines_and_reports_what_fits():
    renderer = _renderer()
    assert renderer.fits(["బాడీ", "నూడుల్స్"])
    assert not renderer.fits(["సాగిపోతుంది!"] * 12)
    one_line = renderer.render(("బాడీ",), 0)
    two_lines = renderer.render(tuple(["నూడుల్స్"] * 6), 0)
    assert two_lines.shape[0] > one_line.shape[0]


def test_word_timeline_round_trip(tmp_path):
    srt = str(tmp_path / "subtitle.srt")
    captions.save_word_timeline(captions.words_path(srt), [{"word": "a", "start": 0.1, "end": 0.4}, {"word": "b", "start": 1, "end": 1}])
    assert captions.load_word_timeline(srt) == [{"word": "a", "start": 0.1, "end": 0.4}]
    assert captions.load_word_timeline(str(tmp_path / "missing.srt")) == []


def test_precise_timing_saves_whisper_words_next_to_the_subtitles(tmp_path):
    params = VideoParams(video_subject="x", subtitle_display_mode="word_highlight", caption_precise_timing=True)
    words = [{"word": "hi", "start": 0.0, "end": 0.5}]
    with patch.object(task.utils, "task_dir", return_value=str(tmp_path)), patch.object(
        task.config, "app", {"subtitle_provider": "edge"}
    ), patch.object(task.voice, "create_subtitle", side_effect=lambda **kw: open(kw["subtitle_file"], "w").write("1\n00:00:00,000 --> 00:00:01,000\nhi\n")), patch.object(
        task.captions, "transcribe_word_timeline", return_value=words
    ):
        path = task.generate_subtitle("t", params, "hi", object(), "audio.mp3")
    assert captions.load_word_timeline(path) == words


def test_shorts_captions_render_in_a_real_video(tmp_path):
    from moviepy import ColorClip

    video_path = str(tmp_path / "in.mp4")
    ColorClip((540, 960), color=(30, 60, 110), duration=2).write_videofile(
        video_path, fps=10, codec="libx264", logger=None
    )
    audio_path = str(tmp_path / "voice.wav")
    assert voice.generate_silent_audio(2.0, audio_path)
    srt = str(tmp_path / "subtitle.srt")
    with open(srt, "w", encoding="utf-8") as file:
        # MoviePy only reads the last entry when the file ends with a blank line.
        file.write(f"1\n00:00:00,000 --> 00:00:02,000\n{TENGLISH}\n\n")
    params = VideoParams(
        video_subject="x",
        video_aspect="9:16",
        subtitle_enabled=True,
        subtitle_display_mode="word_highlight",
        subtitle_position="center",
        font_name="NotoSansTelugu-Bold.ttf",
        font_size=60,
        bgm_type="",
        caption_highlight_color="#00FF00",
    )
    output = str(tmp_path / "out.mp4")
    with patch.object(vd, "VideoAspect") as aspect:
        aspect.return_value.to_resolution.return_value = (540, 960)
        vd.generate_video(video_path, audio_path, srt, output, params)
    assert os.path.getsize(output) > 0

    from moviepy import VideoFileClip

    with VideoFileClip(output) as clip:
        frame = clip.get_frame(0.1)
    green = (frame[:, :, 1] > 200) & (frame[:, :, 0] < 80) & (frame[:, :, 2] < 80)
    assert green.sum() > 50
