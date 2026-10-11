import json
import os
import random
import tempfile
import unittest
from unittest import mock

from app.services import ai_music, elevenlabs_music


class _FakeResponse:
    def __init__(self, status_code=200, body=b"", ok=None):
        self.status_code = status_code
        self.ok = (200 <= status_code < 300) if ok is None else ok
        self.reason = "reason"
        self.encoding = "utf-8"
        self._body = body

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PromptTests(unittest.TestCase):
    def test_random_prompt_always_requests_instrumental_background_music(self):
        prompt = ai_music.build_random_prompt(random.Random(1))

        for constraint in ai_music.BACKGROUND_CONSTRAINTS:
            self.assertIn(constraint, prompt)

    def test_seeded_prompts_are_reproducible_and_vary(self):
        first = ai_music.build_random_prompt(random.Random(7))
        again = ai_music.build_random_prompt(random.Random(7))
        others = {ai_music.build_random_prompt(random.Random(seed)) for seed in range(20)}

        self.assertEqual(first, again)
        self.assertGreater(len(others), 10)

    def test_track_length_is_clamped_to_supported_range(self):
        self.assertEqual(ai_music.clamp_track_seconds(5), ai_music.MIN_TRACK_SECONDS)
        self.assertEqual(ai_music.clamp_track_seconds(900), ai_music.MAX_TRACK_SECONDS)
        self.assertEqual(ai_music.clamp_track_seconds(60), 60)


class GenerateTrackTests(unittest.TestCase):
    def setUp(self):
        patches = [
            mock.patch.object(elevenlabs_music, "is_enabled", return_value=True),
            mock.patch.object(elevenlabs_music, "get_api_key", return_value="test-key"),
            mock.patch.object(elevenlabs_music, "_base_url", return_value="https://api.test"),
            mock.patch.object(elevenlabs_music, "_model_id", return_value="music_v2"),
            mock.patch.object(elevenlabs_music, "_request_timeout", return_value=(15, 60)),
            mock.patch.object(ai_music.bgm_service, "validate_audio_file"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_request_targets_text_music_endpoint_with_length_and_prompt(self):
        audio = b"ID3-fake-mp3" * 10
        with mock.patch.object(
            ai_music.requests, "post", return_value=_FakeResponse(body=audio)
        ) as post:
            result = ai_music.generate_track("calm piano", 45)

        self.assertEqual(result, audio)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.test/v1/music")
        self.assertEqual(kwargs["headers"], {"xi-api-key": "test-key"})
        self.assertEqual(kwargs["params"], {"output_format": "mp3_44100_128"})
        self.assertEqual(kwargs["json"]["prompt"], "calm piano")
        self.assertEqual(kwargs["json"]["music_length_ms"], 45_000)
        self.assertEqual(kwargs["json"]["model_id"], "music_v2")

    def test_api_error_is_reported_with_status_and_body(self):
        failed = _FakeResponse(status_code=401, body=b"invalid api key")
        with mock.patch.object(ai_music.requests, "post", return_value=failed):
            with self.assertRaises(ai_music.AiMusicError) as ctx:
                ai_music.generate_track("calm piano", 30)

        self.assertIn("401", str(ctx.exception))
        self.assertIn("invalid api key", str(ctx.exception))

    def test_empty_prompt_is_rejected_without_a_request(self):
        with mock.patch.object(ai_music.requests, "post") as post:
            with self.assertRaises(ai_music.AiMusicError):
                ai_music.generate_track("   ", 30)

        post.assert_not_called()

    def test_missing_key_is_rejected_without_a_request(self):
        with (
            mock.patch.object(elevenlabs_music, "is_enabled", return_value=False),
            mock.patch.object(ai_music.requests, "post") as post,
        ):
            with self.assertRaises(ai_music.AiMusicError):
                ai_music.generate_track("calm piano", 30)

        post.assert_not_called()


class SaveToLibraryTests(unittest.TestCase):
    def test_saves_track_and_appends_provenance_record(self):
        with tempfile.TemporaryDirectory() as storage:
            with (
                mock.patch.object(ai_music.utils, "storage_dir", return_value=storage),
                mock.patch.object(
                    ai_music.bgm_service,
                    "save_bgm_upload",
                    return_value=os.path.join(storage, "ai-music.mp3"),
                ) as save,
            ):
                path = ai_music.save_to_bgm_library(b"mp3-bytes", "calm piano", 60)

            self.assertTrue(save.call_args[0][0].startswith("ai-music-"))
            self.assertEqual(path, os.path.join(storage, "ai-music.mp3"))
            with open(os.path.join(storage, ai_music.PROVENANCE_LOG_NAME), encoding="utf-8") as log:
                record = json.loads(log.readline())
            self.assertEqual(record["prompt"], "calm piano")
            self.assertEqual(record["seconds"], 60)
            self.assertEqual(record["file"], "ai-music.mp3")

    def test_empty_audio_is_rejected(self):
        with self.assertRaises(ai_music.AiMusicError):
            ai_music.save_to_bgm_library(b"", "calm piano", 60)


if __name__ == "__main__":
    unittest.main()
