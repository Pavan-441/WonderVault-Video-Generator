"""Scene Planner: scenes, Flow prompts and images timed to the narration."""

import json
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from app.config import config
from app.models.schema import MaterialInfo, VideoConcatMode, VideoParams
from app.services import llm, scene_planner, state as sm, task, version_checker, voice

WEBUI_MAIN = Path(__file__).resolve().parents[2] / "webui" / "Main.py"

SCRIPT = (
    "Fall into a black hole feet first, and you become spaghetti. "
    "Gravity at your feet pulls much harder than at your head. "
    "Scientists call it spaghettification. "
    "To a friend far away, you never fall in. "
    "You slow down at the edge, fade to red, and freeze there forever. "
    "Would you jump in?"
)


def test_split_scenes_groups_short_sentences_and_keeps_order():
    scenes = scene_planner.split_scenes(SCRIPT)
    joined = " ".join(scene.text for scene in scenes)
    assert joined == SCRIPT
    assert 3 <= len(scenes) <= 6
    assert all(scene.seconds <= scene_planner.MAX_SCENE_SECONDS + 1 for scene in scenes)
    # The short closing question joins the scene before it.
    assert scenes[-1].text.endswith("freeze there forever. Would you jump in?")


def test_split_scenes_skips_separators_and_pause_tags_and_handles_telugu():
    script = "---\nBlack Hole లోకి పడితే బాడీ నూడుల్స్ లా సాగిపోతుంది! [pause 1s]\n***\nఇది నిజం."
    scenes = scene_planner.split_scenes(script)
    text = " ".join(scene.text for scene in scenes)
    assert "---" not in text and "pause" not in text
    assert "నూడుల్స్" in text


def test_long_scripts_are_capped():
    script = " ".join(f"Fact number {n} is short." for n in range(80))
    assert len(scene_planner.split_scenes(script)) <= scene_planner.MAX_SCENES


def test_scene_durations_share_the_video_by_narration_length():
    assert scene_planner.scene_durations([1, 3], 20.0) == [5.0, 15.0]
    assert sum(scene_planner.scene_durations([2.5, 4.1, 0], 31.2)) == pytest.approx(31.2)


def test_plan_adds_style_and_shape_to_each_prompt():
    scenes = scene_planner.split_scenes(SCRIPT)
    reply = json.dumps({"prompts": [f"scene {n} picture." for n in range(len(scenes))]})
    with patch.object(llm, "_generate_response", return_value=reply) as call:
        plan = scene_planner.plan_scenes(SCRIPT, "Black hole", "kids_cartoon", "portrait", "kids")
    assert not plan.used_fallback
    assert plan.scenes[0].prompt == (
        "scene 0 picture, cute colorful cartoon for kids, soft lighting, friendly characters, "
        "vertical 9:16 composition, no text, no letters, no watermark"
    )
    prompt = call.call_args.kwargs["prompt"]
    assert "young children" in prompt and f"exactly {len(scenes)} prompts" in prompt


def test_plan_falls_back_when_the_model_fails_or_miscounts():
    with patch.object(llm, "_generate_response", side_effect=['{"prompts": ["only one"]}', "Error: down"]):
        plan = scene_planner.plan_scenes(SCRIPT, "Black hole")
    assert plan.used_fallback
    assert plan.scenes[0].prompt.startswith("Black hole: Fall into a black hole")
    assert "## Scene 1" in plan.to_markdown("Black hole")


def test_plan_needs_a_script():
    with pytest.raises(ValueError):
        scene_planner.plan_scenes("  \n---\n")


def test_pipeline_times_each_image_to_its_scene(tmp_path):
    params = VideoParams(
        video_subject="x",
        video_source="local",
        video_materials=[MaterialInfo(provider="local", url=f"img{n}.png") for n in range(3)],
        material_weights=[1.0, 2.0, 1.0],
        video_clip_duration=3,
    )
    calls = []

    def fake_preprocess(materials, clip_duration, image_motion):
        calls.append((materials[0].url, clip_duration))
        return [SimpleNamespace(url=f"{materials[0].url}.mp4")]

    with patch.object(task.video, "preprocess_video", side_effect=fake_preprocess), patch.object(
        task.video, "_get_required_video_duration", return_value=20.0
    ):
        paths = task.get_video_materials("t", params, [], audio_duration=19.8)

    assert paths == ["img0.png.mp4", "img1.png.mp4", "img2.png.mp4"]
    assert calls == [("img0.png", 5.0), ("img1.png", 10.0), ("img2.png", 5.0)]
    assert params.video_concat_mode == VideoConcatMode.sequential
    assert params.video_clip_duration == 11


def test_pipeline_ignores_weights_that_do_not_match_the_images():
    params = VideoParams(
        video_subject="x",
        video_source="local",
        video_materials=[MaterialInfo(provider="local", url="a.png")],
        material_weights=[1.0, 2.0],
        video_clip_duration=4,
    )
    with patch.object(
        task.video, "preprocess_video", return_value=[SimpleNamespace(url="a.mp4")]
    ) as preprocess:
        task.get_video_materials("t", params, [], audio_duration=10)
    assert preprocess.call_args.kwargs["clip_duration"] == 4


@pytest.fixture
def run_webui(tmp_path):
    with ExitStack() as stack:

        def start(ui_config, llm_reply="", session=None):
            stack.enter_context(patch.object(config, "app", {"llm_provider": "openai"}))
            stack.enter_context(patch.object(config, "ui", ui_config))
            stack.enter_context(patch.object(config, "try_save_config", return_value=True))
            stack.enter_context(patch.object(sm.state, "get_all_tasks", return_value=([], 0)))
            stack.enter_context(
                patch.object(voice, "get_all_azure_voices", return_value=["en-US-Jenny"])
            )
            stack.enter_context(patch.object(llm, "_generate_response", return_value=llm_reply))
            stack.enter_context(
                patch.object(
                    version_checker,
                    "poll_available_update",
                    return_value=version_checker.UpdateCheckSnapshot(complete=True),
                )
            )
            stack.enter_context(patch("app.utils.utils.task_dir", return_value=str(tmp_path)))
            app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=60)
            app.session_state["ui_language"] = "en"
            for key, value in (session or {}).items():
                app.session_state[key] = value
            app.run()
            assert not app.exception
            return app

        yield start


def test_planner_tab_writes_prompts_with_one_slot_per_scene(run_webui):
    count = len(scene_planner.split_scenes(SCRIPT))
    reply = json.dumps({"prompts": [f"picture {n}" for n in range(1, count + 1)]})
    app = run_webui({"language": "en"}, reply)
    assert any("Scene Planner" in str(tab.label) for tab in app.tabs)

    app.text_area(key="scene_script").input(SCRIPT).run()
    app.button(key="scene_plan_btn").click().run()

    assert not app.exception
    assert any(c.value.startswith("1. picture 1, photorealistic") for c in app.code)
    assert any(f"0 of {count} scene images added" in c.value for c in app.caption)
    assert app.button(key="scene_use_btn").disabled


def test_sync_copies_the_video_generator_script(run_webui):
    app = run_webui({"language": "en"}, session={"video_script": SCRIPT, "video_subject": "Black hole"})
    app.button(key="scene_sync_btn").click().run()
    assert app.text_area(key="scene_script").value == SCRIPT
    assert app.text_input(key="scene_subject").value == "Black hole"


def test_handoff_fills_the_video_generator(run_webui):
    handoff = {
        "script": SCRIPT,
        "subject": "Black hole",
        "materials": [{"provider": "local", "url": "a.png", "duration": 0}] * 4,
        "weights": [2.0, 3.0, 1.5, 4.0],
    }
    app = run_webui(
        {"language": "en", "video_source": "pexels"}, session={"scene_planner_handoff": handoff}
    )
    assert app.text_area(key="video_script").value == SCRIPT
    assert app.session_state["video_source_select_en"] == "local"
    assert any("Using 4 scene images from the Scene Planner" in c.value for c in app.caption)
    assert app.session_state["local_material_weights"] == [2.0, 3.0, 1.5, 4.0]
