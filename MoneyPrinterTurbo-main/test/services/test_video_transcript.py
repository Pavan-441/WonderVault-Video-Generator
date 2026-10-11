import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from app.services import subtitle, video_transcript


class _FakeWhisper:
    """记录 transcribe 参数，并返回预设的分段（保留标点的原始文本）。"""

    def __init__(self, texts, detected_language="en"):
        self.texts = texts
        self.detected_language = detected_language
        self.calls = []

    def transcribe(self, audio_file, **kwargs):
        self.calls.append((audio_file, kwargs))
        segments = [SimpleNamespace(text=text) for text in self.texts]
        return iter(segments), SimpleNamespace(language=self.detected_language)


def _fake_extract_audio(media_path, wav_path):
    with open(wav_path, "wb") as output:
        output.write(b"RIFF-fake-wav")


class VideoTranscriptTests(unittest.TestCase):
    def _extract(self, whisper, mode, *, filename="clip.mp4"):
        with (
            mock.patch.object(video_transcript, "_extract_audio", side_effect=_fake_extract_audio),
            mock.patch.object(subtitle, "get_model", return_value=whisper),
        ):
            return video_transcript.extract_text(b"video-bytes", filename, mode)

    def test_segments_are_joined_with_punctuation_preserved(self):
        whisper = _FakeWhisper([" Hello there.", " This is a test?", "   "])

        text = self._extract(whisper, video_transcript.LANGUAGE_ENGLISH)

        self.assertEqual(text, "Hello there. This is a test?")

    def test_english_mode_forces_english_language(self):
        whisper = _FakeWhisper(["Hi."])

        self._extract(whisper, video_transcript.LANGUAGE_ENGLISH)

        self.assertEqual(whisper.calls[0][1]["language"], "en")

    def test_telugu_mode_forces_telugu_language(self):
        whisper = _FakeWhisper(["నమస్కారం."], detected_language="te")

        text = self._extract(whisper, video_transcript.LANGUAGE_TELUGU)

        self.assertEqual(whisper.calls[0][1]["language"], "te")
        self.assertEqual(text, "నమస్కారం.")

    def test_mixed_mode_auto_detects_and_uses_bilingual_prompt(self):
        whisper = _FakeWhisper(["ఈ రోజు we will learn."], detected_language="te")

        self._extract(whisper, video_transcript.LANGUAGE_TELUGU_ENGLISH)

        kwargs = whisper.calls[0][1]
        self.assertIsNone(kwargs["language"])
        self.assertIn("English", kwargs["initial_prompt"])
        self.assertIn("తెలుగు", kwargs["initial_prompt"])

    def test_auto_mode_uses_configured_initial_prompt(self):
        whisper = _FakeWhisper(["Hi."])

        with mock.patch.object(subtitle, "initial_prompt", "Brand: MoneyPrinter"):
            self._extract(whisper, video_transcript.LANGUAGE_AUTO)

        kwargs = whisper.calls[0][1]
        self.assertIsNone(kwargs["language"])
        self.assertEqual(kwargs["initial_prompt"], "Brand: MoneyPrinter")

    def test_empty_speech_returns_empty_string(self):
        whisper = _FakeWhisper(["   "])

        self.assertEqual(self._extract(whisper, video_transcript.LANGUAGE_AUTO), "")

    def test_empty_upload_is_rejected(self):
        with self.assertRaises(video_transcript.VideoTranscriptError):
            video_transcript.extract_text(b"", "clip.mp4", video_transcript.LANGUAGE_AUTO)

    def test_file_over_size_limit_is_rejected_before_extraction(self):
        with (
            mock.patch.object(video_transcript, "MAX_VIDEO_TEXT_UPLOAD_BYTES", 16),
            mock.patch.object(video_transcript, "_extract_audio") as extract,
        ):
            with self.assertRaises(video_transcript.VideoTranscriptError) as ctx:
                video_transcript.extract_text(b"x" * 17, "clip.mp4", video_transcript.LANGUAGE_AUTO)

        extract.assert_not_called()
        self.assertIn("limit", str(ctx.exception))

    def test_upload_limit_is_500_mb(self):
        self.assertEqual(video_transcript.MAX_VIDEO_TEXT_UPLOAD_MB, 500)

    def test_unknown_language_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            video_transcript.extract_text(b"video", "clip.mp4", "klingon")

    def test_missing_whisper_model_raises_readable_error(self):
        with (
            mock.patch.object(video_transcript, "_extract_audio", side_effect=_fake_extract_audio),
            mock.patch.object(subtitle, "get_model", return_value=None),
        ):
            with self.assertRaises(video_transcript.VideoTranscriptError) as ctx:
                video_transcript.extract_text(
                    b"video", "clip.mp4", video_transcript.LANGUAGE_AUTO
                )

        self.assertIn("Whisper", str(ctx.exception))

    def test_ffmpeg_failure_is_reported_as_missing_audio(self):
        failed = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"no audio")

        with (
            mock.patch.object(video_transcript.utils, "get_ffmpeg_binary", return_value="ffmpeg"),
            mock.patch.object(video_transcript.subprocess, "run", return_value=failed),
        ):
            with self.assertRaises(video_transcript.VideoTranscriptError) as ctx:
                video_transcript.extract_text(b"video", "clip.mp4", video_transcript.LANGUAGE_AUTO)

        self.assertIn("audio track", str(ctx.exception))

    def test_temp_files_are_removed_after_extraction(self):
        whisper = _FakeWhisper(["Hi."])
        seen_paths = []

        def _record_extract(media_path, wav_path):
            seen_paths.extend([media_path, wav_path])
            _fake_extract_audio(media_path, wav_path)

        with (
            mock.patch.object(video_transcript, "_extract_audio", side_effect=_record_extract),
            mock.patch.object(subtitle, "get_model", return_value=whisper),
        ):
            video_transcript.extract_text(b"video", "clip.mp4", video_transcript.LANGUAGE_AUTO)

        self.assertEqual(len(seen_paths), 2)
        for path in seen_paths:
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
