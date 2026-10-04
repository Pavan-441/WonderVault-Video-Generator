import io
import os
import unittest
from PIL import Image

from app.services import thumbnail


class TestThumbnailService(unittest.TestCase):
    def test_is_telugu_detection(self):
        self.assertTrue(thumbnail.is_telugu("ఈ వీడియోలో మనం చూద్దాం"))
        self.assertTrue(thumbnail.is_telugu("Hello ప్రపంచం"))
        self.assertFalse(thumbnail.is_telugu("This is purely an English script"))
        self.assertFalse(thumbnail.is_telugu(""))

    def test_extract_hook_title_fallback_telugu(self):
        script = "ఈ రోజు మనం అత్యంత రహస్యమైన విషయాన్ని తెలుసుకుందాం"
        title = thumbnail.extract_hook_title_fallback(script, is_telugu_lang=True)
        self.assertTrue(len(title) > 0)
        self.assertTrue(thumbnail.is_telugu(title))

    def test_extract_hook_title_fallback_english(self):
        script = "You will not believe what happened in this ancient temple"
        title = thumbnail.extract_hook_title_fallback(script, is_telugu_lang=False)
        self.assertTrue(len(title) > 0)
        self.assertIn("YOU WILL NOT BELIEVE", title)

    def test_craft_flow_prompt(self):
        prompt_16_9 = thumbnail.craft_flow_prompt(
            script="Exploring the secrets of black holes in deep space",
            aspect_ratio="16:9",
            has_cutout=True,
        )
        self.assertIn("16:9 widescreen", prompt_16_9)
        self.assertIn("negative space", prompt_16_9)

        prompt_9_16 = thumbnail.craft_flow_prompt(
            script="Top 3 shocking historical facts you didn't know",
            aspect_ratio="9:16",
            has_cutout=False,
        )
        self.assertIn("9:16 vertical", prompt_9_16)
        self.assertIn("hero subject", prompt_9_16)

    def test_compose_thumbnail_16_9_english(self):
        # Create a blank dummy background image
        bg = Image.new("RGB", (1280, 720), (30, 40, 60))
        result = thumbnail.compose_thumbnail(
            background_image=bg,
            title_text="SHOCKING TRUTH!",
            aspect_ratio="16:9",
            style_key="yellow_black",
        )
        self.assertEqual(result.size, (1920, 1080))
        self.assertEqual(result.mode, "RGB")

    def test_compose_thumbnail_9_16_telugu_with_cutout(self):
        bg = Image.new("RGB", (720, 1280), (20, 20, 20))
        # Create a dummy transparent cutout
        cutout = Image.new("RGBA", (200, 300), (255, 0, 0, 255))
        result = thumbnail.compose_thumbnail(
            background_image=bg,
            title_text="షాకింగ్ నిజం!",
            cutout_image=cutout,
            aspect_ratio="9:16",
            style_key="neon_cyan",
            cutout_position="right",
        )
        self.assertEqual(result.size, (1080, 1920))
        self.assertEqual(result.mode, "RGB")

    def test_compose_thumbnail_tenglish_mixed_script(self):
        bg = Image.new("RGB", (720, 1280), (10, 20, 30))
        result = thumbnail.compose_thumbnail(
            background_image=bg,
            title_text="ఓషన్ లో  Barreleye",
            aspect_ratio="9:16",
            style_key="yellow_black",
        )
        self.assertEqual(result.size, (1080, 1920))
        self.assertEqual(result.mode, "RGB")

    def test_compose_thumbnail_telugu_complex_ligatures(self):
        bg = Image.new("RGB", (1280, 720), (10, 20, 30))
        result = thumbnail.compose_thumbnail(
            background_image=bg,
            title_text="గాంధీ ఒక్కరే కాదు\nఅసలు నిజం",
            aspect_ratio="16:9",
            style_key="yellow_black",
            add_badge=True,
        )
        self.assertEqual(result.size, (1920, 1080))
        self.assertEqual(result.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
