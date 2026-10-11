import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

# Import the app package before Pillow's font module, as the real entry points
# do: app/__init__.py puts the bundled FriBiDi DLL on PATH for text shaping.
import app  # noqa: F401  isort: skip
from app.services import video as vd  # isort: skip
from PIL import ImageFont, features  # isort: skip
from app.utils import utils

TENGLISH = "బ్లాక్ హోల్ లో పడితే Black Hole 2026! సముద్రంలో కొత్త Barreleye చేప"


def _font(name):
    return os.path.join(utils.font_dir(), name)


class TestTenglishFonts(unittest.TestCase):
    def test_tenglish_fonts_cover_telugu_and_english(self):
        for name in vd.TENGLISH_FONTS.values():
            self.assertTrue(vd.subtitle_font_supports_text(_font(name), TENGLISH), name)

    def test_telugu_only_font_misses_english(self):
        # The original bug: English words became boxes with Noto Sans Telugu.
        self.assertFalse(vd.subtitle_font_supports_text(_font("NotoSansTelugu-Bold.ttf"), TENGLISH))

    def test_fallback_matches_selected_weight(self):
        bold = vd.resolve_subtitle_font(_font("NotoSansTelugu-Bold.ttf"), TENGLISH)
        regular = vd.resolve_subtitle_font(_font("NotoSansTelugu-Regular.ttf"), TENGLISH)
        self.assertEqual(os.path.basename(bold), "TenglishSans-Bold.ttf")
        self.assertEqual(os.path.basename(regular), "TenglishSans-Regular.ttf")

    def test_latin_only_font_falls_back_for_telugu_text(self):
        resolved = vd.resolve_subtitle_font(_font("BeVietnamPro-Bold.ttf"), TENGLISH)
        self.assertEqual(os.path.basename(resolved), "TenglishSans-Bold.ttf")

    def test_supported_font_is_kept(self):
        path = _font("NotoSansTelugu-Bold.ttf")
        self.assertEqual(vd.resolve_subtitle_font(path, "బ్లాక్ హోల్ లో పడితే"), path)
        self.assertEqual(vd.resolve_subtitle_font("", TENGLISH), "")

    def test_uncoverable_text_keeps_original_font(self):
        path = _font("NotoSansTelugu-Bold.ttf")
        self.assertEqual(vd.resolve_subtitle_font(path, "ሰላም ዓለም"), path)


@unittest.skipUnless(os.name == "nt", "bundled FriBiDi DLL is Windows-only")
class TestTeluguShaping(unittest.TestCase):
    """Run in a fresh interpreter: Pillow decides on shaping when its font module
    first loads, and other test modules may have loaded it before app/__init__.py."""

    def _run(self, code):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        return result.stdout.strip().splitlines()[-1]

    def test_app_import_enables_raqm(self):
        # Same order as the real entry points: app first, then MoviePy/Pillow.
        code = chr(10).join([
            "import app",
            "import app.services.video",
            "from PIL import features",
            "print(features.check('raqm'))",
        ])
        self.assertEqual(self._run(code), "True")

    def test_conjuncts_are_shaped(self):
        # Basic layout draws the halant and second consonant side by side;
        # HarfBuzz forms the conjunct (vottu below), which is narrower.
        code = chr(10).join([
            "import app",
            "from PIL import ImageFont",
            "p = 'resource/fonts/TenglishSans-Bold.ttf'",
            "basic = ImageFont.truetype(p, 80, layout_engine=ImageFont.Layout.BASIC)",
            "shaped = ImageFont.truetype(p, 80, layout_engine=ImageFont.Layout.RAQM)",
            "words = [chr(0x0C38) + chr(0x0C2E) + chr(0x0C41) + chr(0x0C26) + chr(0x0C4D) + chr(0x0C30),",
            "         chr(0x0C15) + chr(0x0C4A) + chr(0x0C24) + chr(0x0C4D) + chr(0x0C24)]",
            "print(all(shaped.getlength(w) < basic.getlength(w) for w in words))",
        ])
        self.assertEqual(self._run(code), "True")


class _StopRendering(Exception):
    pass


class TestGenerateVideoUsesTenglishFont(unittest.TestCase):
    def test_generate_video_switches_font_before_building_subtitles(self):
        with tempfile.TemporaryDirectory() as tmp:
            srt = os.path.join(tmp, "subtitle.srt")
            with open(srt, "w", encoding="utf-8") as f:
                f.write(f"1\n00:00:00,000 --> 00:00:02,000\n{TENGLISH}\n")
            params = vd.VideoParams(
                video_subject="test",
                subtitle_enabled=True,
                font_name="NotoSansTelugu-Bold.ttf",
                bgm_type="",
            )
            original_resolve = vd.resolve_subtitle_font
            resolved = []

            def spy_resolve(font_path, text):
                resolved.append((font_path, text, original_resolve(font_path, text)))
                return resolved[-1][2]

            with (
                patch.object(vd, "_open_video_clip_quietly"),
                patch.object(vd, "AudioFileClip"),
                patch.object(vd, "resolve_subtitle_font", side_effect=spy_resolve),
                patch.object(vd, "SubtitlesClip", side_effect=_StopRendering),
            ):
                with self.assertRaises(_StopRendering):
                    vd.generate_video(
                        video_path="combined.mp4",
                        audio_path="voice.mp3",
                        subtitle_path=srt,
                        output_file=os.path.join(tmp, "final.mp4"),
                        params=params,
                    )
        self.assertEqual(len(resolved), 1)
        selected, text, used = resolved[0]
        self.assertEqual(os.path.basename(selected), "NotoSansTelugu-Bold.ttf")
        self.assertIn("Barreleye", text)
        self.assertEqual(os.path.basename(used), "TenglishSans-Bold.ttf")


if __name__ == "__main__":
    unittest.main()
