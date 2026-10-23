"""验证素材下载、片段处理和最终编码阶段的终端进度日志与进度条推进。"""

import os
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.config import config
from app.models.schema import VideoParams
from app.services import material
from app.services import task as tm
from app.services import video as vd
from app.services.state import MemoryState


class _ChunkedResponse:
    def __init__(self, chunks, headers=None, fail_after=None):
        self.chunks = chunks
        self.headers = headers or {}
        self.fail_after = fail_after

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        for index, chunk in enumerate(self.chunks):
            if self.fail_after is not None and index == self.fail_after:
                raise ConnectionError("connection reset")
            yield chunk


class _FakeVideoFileClip:
    duration = 1
    fps = 24

    def __init__(self, path):
        pass

    def close(self):
        pass


def _save_video(tmp_path, response):
    with (
        patch("app.services.material.requests.get", return_value=response) as get,
        patch("app.services.material.VideoFileClip", _FakeVideoFileClip),
        patch.object(material.logger, "info") as info,
    ):
        video_path = material.save_video(
            "https://example.com/large.mp4?key=secret", save_dir=str(tmp_path)
        )
    messages = [str(call.args[0]) for call in info.call_args_list]
    return video_path, messages, get


MEGABYTE = b"x" * (1024 * 1024)


def test_slow_download_logs_size_percent_and_speed_without_url(tmp_path):
    with patch.object(material, "_DOWNLOAD_HEARTBEAT_SECONDS", 0):
        video_path, messages, get = _save_video(
            tmp_path,
            _ChunkedResponse(
                [MEGABYTE] * 3, headers={"Content-Length": str(3 * 1024 * 1024)}
            ),
        )

    assert get.call_args.kwargs["stream"] is True
    assert os.path.getsize(video_path) == 3 * 1024 * 1024
    heartbeats = [m for m in messages if m.startswith("downloading video")]
    assert len(heartbeats) == 3
    assert heartbeats[0].startswith("downloading video vid-")
    assert "1.0 of 3.0 MB (33%)" in heartbeats[0]
    assert "3.0 of 3.0 MB (100%)" in heartbeats[2]
    assert not [m for m in messages if "secret" in m or "example.com" in m]


def test_download_without_declared_size_still_logs_megabytes(tmp_path):
    with patch.object(material, "_DOWNLOAD_HEARTBEAT_SECONDS", 0):
        _, messages, _ = _save_video(tmp_path, _ChunkedResponse([MEGABYTE] * 2))

    heartbeats = [m for m in messages if m.startswith("downloading video")]
    assert len(heartbeats) == 2
    assert heartbeats[1].split(": ", 1)[1].startswith("2.0 MB, ")


def test_fast_download_stays_quiet(tmp_path):
    _, messages, _ = _save_video(tmp_path, _ChunkedResponse([b"small", b"video"]))
    assert not [m for m in messages if m.startswith("downloading video")]


def test_interrupted_download_leaves_no_cached_or_partial_file(tmp_path):
    with pytest.raises(ConnectionError):
        _save_video(tmp_path, _ChunkedResponse([MEGABYTE] * 3, fail_after=1))
    # 半截文件若留在缓存路径，下次会被当作“已存在”直接复用。
    assert list(tmp_path.iterdir()) == []


def _items(*names):
    return [
        material.MaterialInfo(
            provider="pixabay",
            url=f"https://v.example/{name}.mp4",
            duration=6,
            source_info={"provider": "pixabay", "asset_id": name},
        )
        for name in names
    ]


def _download(progress_callback, *, concurrency=1, fail=(), **kwargs):
    def fake_save_video(video_url, save_dir=""):
        name = video_url.rsplit("/", 1)[-1]
        if name in fail:
            raise RuntimeError("network down")
        return f"/tmp/{name}"

    with (
        patch.dict(config.app, {"material_directory": ""}),
        patch.object(material, "search_videos_pixabay", return_value=_items("a", "b")),
        patch.object(material, "save_video", side_effect=fake_save_video),
        patch.object(material.material_cache, "load_material_search_cache", return_value=None),
        patch.object(material.material_cache, "save_material_search_cache"),
        patch.object(material, "_persist_material_sources"),
        patch.object(material.logger, "info") as info,
    ):
        result = material.download_videos(
            task_id="download-progress",
            search_terms=["city"],
            source="pixabay",
            video_concat_mode=material.VideoConcatMode.sequential,
            audio_duration=10,
            max_clip_duration=6,
            stock_material_concurrency=concurrency,
            progress_callback=progress_callback,
            **kwargs,
        )
    messages = [str(call.args[0]) for call in info.call_args_list]
    return result, messages


@pytest.mark.parametrize("concurrency", [1, 4])
def test_download_reports_covered_duration_and_logs_each_material(concurrency):
    fractions = []
    result, messages = _download(fractions.append, concurrency=concurrency)

    assert sorted(result) == ["/tmp/a.mp4", "/tmp/b.mp4"]
    assert fractions == [0.6, 1.0]
    finished = [m for m in messages if m.startswith("downloaded material")]
    assert [m.split(":")[0] for m in finished] == [
        "downloaded material 1/2",
        "downloaded material 2/2",
    ]


def test_failed_material_is_not_counted_as_progress():
    fractions = []
    result, messages = _download(fractions.append, fail=("b.mp4",))

    assert result == ["/tmp/a.mp4"]
    assert fractions == [0.6]
    assert [m for m in messages if m.startswith("downloaded material")] == [
        "downloaded material 1/2: a.mp4"
    ]


def test_script_order_download_reports_progress():
    fractions = []
    result, _ = _download(fractions.append, match_script_order=True)

    assert result
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0


def test_failing_progress_callback_does_not_break_download():
    def broken(_fraction):
        raise RuntimeError("state backend unavailable")

    with patch.object(material.logger, "warning") as warning:
        result, _ = _download(broken)

    assert result == ["/tmp/a.mp4", "/tmp/b.mp4"]
    assert [c for c in warning.call_args_list if "progress" in str(c.args[0])]


def test_stage_progress_reporter_maps_fraction_into_stage_range():
    state = MemoryState()
    state.update_task("stage-progress", progress=40)

    with patch.object(tm.sm, "state", state):
        report = tm._stage_progress_reporter("stage-progress", 40, 50)
        for fraction, expected in ((0.0, 40), (0.55, 45), (1.0, 50), (3.0, 50), (-1.0, 40)):
            report(fraction)
            assert state.get_task("stage-progress")["progress"] == expected
        report(0.5)
        report("not a number")
        task = state.get_task("stage-progress")

    assert task["progress"] == 45
    assert task["state"] == tm.const.TASK_STATE_PROCESSING


def test_stage_progress_reporter_survives_state_backend_failure():
    with (
        patch.object(tm.sm.state, "update_task", side_effect=RuntimeError("redis down")),
        patch.object(tm.logger, "warning") as warning,
    ):
        tm._stage_progress_reporter("stage-progress", 40, 50)(0.5)

    warning.assert_called_once()
    assert "redis down" in str(warning.call_args.args[0])


def test_material_download_moves_task_progress_between_40_and_50():
    params = VideoParams(video_subject="test", video_source="pexels")
    state = MemoryState()
    state.update_task("download-progress", progress=40)
    observed = []

    def fake_download_videos(**kwargs):
        for fraction in (0.25, 0.5, 1.0):
            kwargs["progress_callback"](fraction)
            observed.append(state.get_task("download-progress")["progress"])
        return ["a.mp4"]

    with (
        patch.object(tm.sm, "state", state),
        patch.object(tm.material, "download_videos", side_effect=fake_download_videos),
    ):
        assert tm.get_video_materials("download-progress", params, ["scene"], 10) == ["a.mp4"]

    # MemoryState 以整数保存进度。
    assert observed == [42, 45, 50]


class _FakeAudioClip:
    duration = 4.0

    def close(self):
        pass


class _FakeVideoClip:
    duration = 10.0
    size = (1080, 1920)

    def close(self):
        pass


def _combine(progress_callback, concurrency=1):
    def fake_process(i, item, *args):
        return SimpleNamespace(
            duration=2.0, file_path=f"clip-{i}.mp4", source_file_path=item.file_path
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        with (
            patch.object(vd, "AudioFileClip", return_value=_FakeAudioClip()),
            patch.object(vd, "_open_video_clip_quietly", return_value=_FakeVideoClip()),
            patch.object(vd, "_process_single_subclip", side_effect=fake_process),
            patch.object(vd, "concat_video_clips_with_ffmpeg") as concat,
            patch.object(vd, "delete_files"),
            patch.object(vd.logger, "info") as info,
        ):
            result = vd.combine_videos(
                combined_video_path=os.path.join(temp_dir, "combined.mp4"),
                # 顺序模式下每个源文件只取一段，三个源文件对应三段。
                video_paths=["a.mp4", "b.mp4", "c.mp4"],
                audio_file="audio.mp3",
                video_concat_mode=vd.VideoConcatMode.sequential,
                max_clip_duration=2,
                clip_rendering_concurrency=concurrency,
                progress_callback=progress_callback,
            )
    messages = [str(c.args[0]) for c in info.call_args_list]
    return result, messages, concat


@pytest.mark.parametrize("concurrency", [1, 3])
def test_combine_videos_reports_each_processed_clip(concurrency):
    fractions = []
    _, messages, concat = _combine(fractions.append, concurrency)

    # 配音 4.0 秒加 0.1 秒安全余量，每段 2 秒：需要 3 段才覆盖 4.1 秒。
    assert len(fractions) == 3
    assert fractions == sorted(fractions)
    assert fractions[0] == pytest.approx(2.0 / 4.1, abs=1e-3)
    assert fractions[-1] == 1.0
    assert [m for m in messages if m.startswith("processed clip")] == [
        "processed clip 1/3: 2.0 of 4.1s covered",
        "processed clip 2/3: 4.0 of 4.1s covered",
        "processed clip 3/3: 6.0 of 4.1s covered",
    ]
    concat.assert_called_once()


def test_failing_clip_progress_callback_does_not_break_combine():
    def broken(_fraction):
        raise RuntimeError("state backend unavailable")

    with patch.object(vd.logger, "warning") as warning:
        result, _, concat = _combine(broken)

    assert result.endswith("combined.mp4")
    concat.assert_called_once()
    assert [c for c in warning.call_args_list if "progress" in str(c.args[0])]


def test_clip_processing_moves_progress_through_the_combine_half():
    for video_count, expected in ((1, [[62, 75]]), (2, [[56, 62], [81, 87]])):
        params = VideoParams(video_subject="test", video_count=video_count)
        state = MemoryState()
        state.update_task("combine-progress", progress=50)
        observed = []

        def fake_combine_videos(**kwargs):
            seen = []
            for fraction in (0.5, 1.0):
                kwargs["progress_callback"](fraction)
                seen.append(state.get_task("combine-progress")["progress"])
            observed.append(seen)

        with (
            patch.object(tm.sm, "state", state),
            patch.object(tm.video, "combine_videos", side_effect=fake_combine_videos),
            patch.object(tm.video, "generate_video"),
            patch.object(tm.task_artifacts, "patch_script_data"),
        ):
            tm.generate_final_videos(
                task_id="combine-progress",
                params=params,
                downloaded_videos=["material.mp4"],
                audio_file="audio.mp3",
                subtitle_path="",
                audio_duration=5,
            )

        assert observed == expected


def _heartbeats(info):
    return [str(c.args[0]) for c in info.call_args_list if "still running" in str(c.args[0])]


def test_stage_heartbeat_logs_while_running_and_stops_afterwards():
    with (
        patch.object(vd, "_STAGE_HEARTBEAT_SECONDS", 0.02),
        patch.object(vd.logger, "info") as info,
    ):
        with vd._stage_heartbeat("final video render"):
            time.sleep(0.2)
        at_exit = len(_heartbeats(info))
        time.sleep(0.1)
        later = len(_heartbeats(info))

    assert at_exit > 0
    assert later == at_exit
    assert _heartbeats(info)[0].startswith("final video render still running: elapsed=")


def test_stage_heartbeat_stops_when_the_stage_fails():
    before = threading.active_count()
    with (
        patch.object(vd, "_STAGE_HEARTBEAT_SECONDS", 0.02),
        patch.object(vd.logger, "info") as info,
    ):
        with pytest.raises(RuntimeError, match="encode failed"):
            with vd._stage_heartbeat("final video render"):
                raise RuntimeError("encode failed")
        time.sleep(0.1)

    assert _heartbeats(info) == []
    assert threading.active_count() <= before
