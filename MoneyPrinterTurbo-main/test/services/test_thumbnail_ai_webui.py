import ast
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services.thumbnail_ai import generator

WEBUI_MAIN = Path(__file__).parent.parent.parent / "webui" / "Main.py"

# The real thumbnail UI functions from Main.py, run in a small harness. Rerunning
# the full app in AppTest is not possible here: its voice selectbox breaks
# AppTest widget-state round-trips (unrelated to the thumbnail UI).
_HARNESS_NAMES = {
    "GOOGLE_FLOW_URL",
    "THUMBNAIL_QUICK_EDITS",
    "_thumbnail_concept_key",
    "_render_thumbnail_concept_editor",
    "_collect_thumbnail_concepts",
    "_render_thumbnail_variant_card",
}


def _extract_ui_source() -> str:
    source = WEBUI_MAIN.read_text(encoding="utf-8")
    nodes = []
    for node in ast.parse(source).body:
        names = (
            [t.id for t in node.targets if isinstance(t, ast.Name)]
            if isinstance(node, ast.Assign)
            else [getattr(node, "name", "")]
        )
        if _HARNESS_NAMES.intersection(names):
            nodes.append(ast.get_source_segment(source, node))
    return "\n\n".join(nodes)


HARNESS = f"""
import os
import streamlit as st
from app.services import thumbnail_ai

def tr(key):
    return key

exec({_extract_ui_source()!r})

if st.session_state.get("harness_mode") == "concepts":
    script = st.text_area("script", key="thumb_ai_script")
    styles = list(thumbnail_ai.STYLE_VARIANTS)
    _render_thumbnail_concept_editor(thumbnail_ai, script, "", "", styles)
    st.session_state["harness_collected"] = _collect_thumbnail_concepts(thumbnail_ai, styles)
else:
    for idx, variant in enumerate(st.session_state.get("thumb_ai_variants", [])):
        _render_thumbnail_variant_card(thumbnail_ai, idx, variant)
"""


def _png(color=(30, 60, 120)):
    buf = io.BytesIO()
    Image.new("RGB", (1536, 1024), color).save(buf, format="PNG")
    return buf.getvalue()


def _by_key(widgets, fragment):
    for widget in widgets:
        if fragment in str(getattr(widget, "key", "") or ""):
            return widget
    raise AssertionError(f"widget not found: {fragment}")


class TestThumbnailAiWebui(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patchers = [
            mock.patch.object(generator.utils, "storage_dir", return_value=self.tmp),
            # Never write the developer's config.toml from a UI test.
            mock.patch.object(config, "save_config"),
            mock.patch.object(config, "try_save_config", return_value=True),
            # A locally saved voice outside the listed options breaks AppTest
            # widget-state round-trips; it is unrelated to the thumbnail UI.
            mock.patch.dict(config.ui, {"voice_name": "", "video_language": "en-US"}),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _card_app(self, **plan_kwargs):
        plan = dict(script="s", title="TITLE", style_keys=["shock_reaction"])
        plan.update(plan_kwargs)
        variant = generator.plan_variants(**plan)[0]
        app = AppTest.from_string(HARNESS, default_timeout=60)
        app.session_state["thumb_ai_variants"] = [variant]
        app.session_state["thumb_ai_run_id"] = 1
        app.run()
        self.assertFalse(app.exception, app.exception)
        return app, variant

    def _upload(self, app, variant, color):
        uploader = _by_key(app.file_uploader, f"thumb_ai_upload_1_0_{len(variant.versions)}")
        uploader.set_value(("flow.png", _png(color), "image/png")).run()
        self.assertFalse(app.exception, app.exception)

    def test_flow_mode_renders_inputs(self):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=60)
        app.session_state["ui_language"] = "en"
        app.run()
        self.assertFalse(app.exception, app.exception)
        _by_key(app.button, "thumb_ai_plan_btn")
        _by_key(app.button, "thumb_ai_create_btn")
        _by_key(app.multiselect, "thumb_ai_styles")
        _by_key(app.checkbox, "thumb_ai_has_photo")

    def test_card_shows_prompt_then_upload_creates_version(self):
        app, variant = self._card_app()
        self.assertTrue(any('"TITLE"' in c.value for c in app.code))
        self.assertEqual(len(app.get("imgs")), 0)

        self._upload(app, variant, (10, 20, 200))
        self.assertEqual(len(variant.versions), 1)
        # A fresh, empty uploader replaces the used one so the next image can be added.
        self.assertIsNone(_by_key(app.file_uploader, "thumb_ai_upload_1_0_1").value)

    def test_edit_prompt_round_trip_records_instruction(self):
        app, variant = self._card_app()
        self._upload(app, variant, (10, 20, 200))

        _by_key(app.pills, "thumb_ai_quick_1_0").set_value("Thumbnail Quick Add Arrow").run()
        self.assertIn("red arrow", _by_key(app.text_input, "thumb_ai_edit_1_0").value)
        _by_key(app.button, "thumb_ai_edit_btn_1_0").click().run()
        self.assertFalse(app.exception, app.exception)
        self.assertTrue(any("CHANGE: Add one bold red arrow" in c.value for c in app.code))

        self._upload(app, variant, (200, 0, 0))
        self.assertEqual(len(variant.versions), 2)
        self.assertIn("red arrow", variant.versions[1].instruction)
        self.assertEqual(_by_key(app.radio, "thumb_ai_version_1_0").value, 1)
        # The used edit prompt is cleared after its result is uploaded.
        self.assertFalse(any("CHANGE:" in c.value for c in app.code))

        _by_key(app.radio, "thumb_ai_version_1_0").set_value(0).run()
        self.assertFalse(app.exception, app.exception)

    def test_overlay_mode_offers_flow_original_for_editing(self):
        app, variant = self._card_app(title="బాడీ నూడుల్స్!", overlay_text=True)
        self.assertTrue(any("Do NOT render any text" in c.value for c in app.code))
        self._upload(app, variant, (10, 20, 200))
        _by_key(app.text_input, "thumb_ai_edit_1_0").set_value("brighter").run()
        _by_key(app.button, "thumb_ai_edit_btn_1_0").click().run()
        self.assertFalse(app.exception, app.exception)
        self.assertTrue(any("Thumbnail Edit Attach Original Note" in c.value for c in app.caption))

    def test_planned_concepts_become_editable_fields(self):
        app = AppTest.from_string(HARNESS, default_timeout=60)
        app.session_state["harness_mode"] = "concepts"
        app.run()
        _by_key(app.text_area, "thumb_ai_script").set_value("Black holes stretch you like spaghetti.")
        concepts = [generator.Concept("shock_reaction", "BODY BECOMES NOODLES", "", "Shocked astronaut")]
        with mock.patch("app.services.thumbnail_ai.generate_concepts", return_value=concepts):
            _by_key(app.button, "thumb_ai_plan_btn").click().run()
        self.assertFalse(app.exception, app.exception)
        headline = _by_key(app.text_input, "thumb_ai_concept_shock_reaction_headline")
        self.assertEqual(headline.value, "BODY BECOMES NOODLES")
        self.assertEqual(_by_key(app.text_area, "thumb_ai_concept_shock_reaction_scene").value, "Shocked astronaut")

        headline.set_value("NOODLE BODY").run()
        collected = app.session_state["harness_collected"]
        self.assertEqual([(c.style_key, c.headline) for c in collected], [("shock_reaction", "NOODLE BODY")])


if __name__ == "__main__":
    unittest.main()
