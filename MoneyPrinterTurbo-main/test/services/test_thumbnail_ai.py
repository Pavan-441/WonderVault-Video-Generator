import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image

from app.services.thumbnail_ai import brief, generator, postprocess, prompts


def _png_bytes(size=(1536, 1024), color=(40, 80, 160)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class TestFlowPrompts(unittest.TestCase):
    def test_full_prompt_has_every_element(self):
        prompt = prompts.build_prompt(
            topic="black holes",
            title="BODY BECOMES SPAGHETTI",
            subtitle="Real science",
            badge_text="Wonder Vault",
            aspect_ratio="16:9",
            has_reference=True,
            scene="Shocked astronaut next to a black hole",
        )
        for expected in (
            "Aspect ratio 16:9",
            "TOPIC: black holes",
            "SCENE: Shocked astronaut next to a black hole",
            "SUBJECT: Use the person or object from the attached photo",
            '- Headline: "BODY BECOMES SPAGHETTI"',
            '- Secondary line: "Real science"',
            '- Corner badge: "Wonder Vault"',
            "LAYOUT: Text block on the left half",
            "bottom-right corner",
            "AVOID:",
        ):
            self.assertIn(expected, prompt)
        self.assertNotIn("Telugu", prompt)

    def test_telugu_text_gets_exact_script_instruction(self):
        prompt = prompts.build_prompt(topic="t", title="బాడీ నూడుల్స్!", aspect_ratio="9:16")
        self.assertIn('"బాడీ నూడుల్స్!"', prompt)
        self.assertIn("Telugu script", prompt)
        self.assertIn("Aspect ratio 9:16", prompt)
        self.assertIn("bottom quarter", prompt)
        self.assertNotIn("ALL CAPS", prompt)
        self.assertIn("ALL CAPS", prompts.build_prompt(topic="t", title="SHOCK"))

    def test_overlay_mode_forbids_text_and_reserves_zone(self):
        prompt = prompts.build_prompt(topic="t", title="బాడీ నూడుల్స్!", aspect_ratio="16:9", render_text=False)
        self.assertIn("Do NOT render any text", prompt)
        self.assertIn("upper-left area", prompt)
        self.assertNotIn("బాడీ", prompt)

    def test_styles_have_distinct_typography(self):
        rendered = {prompts.build_prompt("topic", "TITLE", style_key=key) for key in prompts.STYLE_VARIANTS}
        self.assertEqual(len(rendered), len(prompts.STYLE_VARIANTS))

    def test_no_photo_means_no_subject_section(self):
        self.assertNotIn("SUBJECT:", prompts.build_prompt("topic", "TITLE"))

    def test_summarize_topic_cuts_at_sentence_boundary(self):
        summary = prompts.summarize_topic("First sentence here. " * 40, limit=100)
        self.assertLessEqual(len(summary), 100)
        self.assertTrue(summary.endswith("."))


class TestPostprocess(unittest.TestCase):
    def test_fit_canvas_exact_sizes(self):
        self.assertEqual(postprocess.fit_canvas(_png_bytes((1536, 1024)), "16:9").size, (1280, 720))
        self.assertEqual(postprocess.fit_canvas(_png_bytes((1024, 1536)), "9:16").size, (1080, 1920))

    def test_encode_stays_under_youtube_limit(self):
        noisy = Image.effect_noise((1280, 720), 120).convert("RGB")
        data, ext = postprocess.encode_for_upload(noisy)
        self.assertLessEqual(len(data), postprocess.MAX_UPLOAD_BYTES)
        self.assertIn(ext, ("png", "jpg"))


class TestBrief(unittest.TestCase):
    def test_parses_fenced_json_and_clamps_headline(self):
        response = """```json
        {"shock_reaction": {"headline": "one two three four five six seven", "subtitle": "wow", "scene": "A shocked man"},
         "cinematic_mystery": {"headline": "THE DARK SECRET", "subtitle": "", "scene": "Dark cave"}}
        ```"""
        with mock.patch("app.services.llm._generate_response", return_value=response):
            concepts = brief.generate_concepts(
                "script", title="TITLE", style_keys=["shock_reaction", "cinematic_mystery", "bold_explainer"]
            )
        by_key = {c.style_key: c for c in concepts}
        self.assertEqual(by_key["shock_reaction"].headline, "one two three four five")
        self.assertEqual(by_key["shock_reaction"].scene, "A shocked man")
        self.assertEqual(by_key["cinematic_mystery"].headline, "THE DARK SECRET")
        self.assertEqual(by_key["bold_explainer"].headline, "TITLE")

    def test_llm_failure_falls_back_to_user_text(self):
        with mock.patch("app.services.llm._generate_response", side_effect=RuntimeError("no llm")):
            concepts = brief.generate_concepts("script", title="MY TITLE", subtitle="sub")
        self.assertEqual([c.headline for c in concepts], ["MY TITLE"] * len(prompts.DEFAULT_STYLE_KEYS))
        self.assertTrue(all(c.subtitle == "sub" for c in concepts))

    def test_telugu_script_requests_telugu_headlines(self):
        with mock.patch("app.services.llm._generate_response", return_value="{}") as llm_call:
            brief.generate_concepts("బ్లాక్ హోల్ లో పడితే", title="")
        self.assertIn("Telugu", llm_call.call_args.kwargs["prompt"])


class TestFlowWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        patcher = mock.patch.object(generator.utils, "storage_dir", return_value=self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_plan_creates_one_prompt_per_style_without_files(self):
        variants = generator.plan_variants(script="Black holes stretch you.", title="SPAGHETTI!")
        self.assertEqual([v.style_key for v in variants], prompts.DEFAULT_STYLE_KEYS)
        self.assertTrue(all('"SPAGHETTI!"' in v.prompt for v in variants))
        self.assertTrue(all(not v.versions for v in variants))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_concepts_drive_prompt_text_and_scene(self):
        concept = brief.Concept("cinematic_mystery", "HIDDEN LAKE", "under ice", "A glowing lake under ice")
        variants = generator.plan_variants(script="s", title="ignored", concepts=[concept], has_reference=True)
        self.assertEqual(len(variants), 1)
        self.assertIn('"HIDDEN LAKE"', variants[0].prompt)
        self.assertIn("SCENE: A glowing lake under ice", variants[0].prompt)
        self.assertIn("SUBJECT:", variants[0].prompt)
        self.assertNotIn("ignored", variants[0].prompt)

    def test_upload_becomes_fitted_version(self):
        variant = generator.plan_variants(script="s", title="TITLE", style_keys=["shock_reaction"])[0]
        version = generator.add_flow_image(variant, _png_bytes((1376, 768)))
        self.assertIs(variant.current, version)
        self.assertTrue(os.path.isfile(version.file_path))
        self.assertTrue(version.file_path.endswith("shock_reaction_v1.png"))
        self.assertEqual(Image.open(io.BytesIO(version.image_bytes)).size, (1280, 720))

    def test_same_upload_twice_is_ignored(self):
        variant = generator.plan_variants(script="s", title="TITLE", style_keys=["shock_reaction"])[0]
        image = _png_bytes()
        self.assertIsNotNone(generator.add_flow_image(variant, image))
        self.assertIsNone(generator.add_flow_image(variant, image))
        self.assertEqual(len(variant.versions), 1)

    def test_overlay_mode_draws_telugu_text_and_keeps_original(self):
        variant = generator.plan_variants(
            script="s", title="బాడీ నూడుల్స్!", style_keys=["bold_explainer"], aspect_ratio="9:16", overlay_text=True
        )[0]
        self.assertIn("Do NOT render any text", variant.prompt)
        original = _png_bytes((1024, 1536))
        with mock.patch.object(
            generator.thumbnail, "compose_thumbnail", wraps=generator.thumbnail.compose_thumbnail
        ) as compose:
            version = generator.add_flow_image(variant, original)
        compose.assert_called_once()
        self.assertEqual(compose.call_args.kwargs["title_text"], "బాడీ నూడుల్స్!")
        self.assertEqual(version.source_bytes, original)
        self.assertEqual(Image.open(io.BytesIO(version.image_bytes)).size, (1080, 1920))

    def test_flow_text_mode_does_not_overlay(self):
        variant = generator.plan_variants(script="s", title="బాడీ నూడుల్స్!", style_keys=["bold_explainer"])[0]
        with mock.patch.object(generator.thumbnail, "compose_thumbnail") as compose:
            generator.add_flow_image(variant, _png_bytes())
        compose.assert_not_called()

    def test_edit_prompt_keeps_text_and_records_instruction_on_upload(self):
        variant = generator.plan_variants(
            script="s", title="TITLE", subtitle="SUB", badge_text="WV", style_keys=["shock_reaction"], has_reference=True
        )[0]
        generator.add_flow_image(variant, _png_bytes())
        prompt = generator.edit_prompt(variant, "add a red arrow")
        self.assertIn("CHANGE: add a red arrow", prompt)
        self.assertIn('"TITLE", "SUB", "WV"', prompt)
        self.assertIn("SUBJECT:", prompt)
        version = generator.add_flow_image(variant, _png_bytes(color=(200, 0, 0)), instruction="add a red arrow")
        self.assertEqual(len(variant.versions), 2)
        self.assertEqual(version.instruction, "add a red arrow")
        self.assertTrue(version.file_path.endswith("_v2.png"))

    def test_overlay_edit_prompt_forbids_text(self):
        variant = generator.plan_variants(script="s", title="బాడీ", style_keys=["shock_reaction"], overlay_text=True)[0]
        self.assertIn("Do NOT add any text", generator.edit_prompt(variant, "brighter"))

    def test_empty_edit_instruction_is_rejected(self):
        variant = generator.plan_variants(script="s", title="TITLE", style_keys=["shock_reaction"])[0]
        with self.assertRaises(ValueError):
            generator.edit_prompt(variant, "   ")


if __name__ == "__main__":
    unittest.main()
