"""Batch videos: renders script-library entries one after another in the background.

Each finished video gets its own folder in the download directory with the video,
its upload kit, a thumbnail prompt for Google Flow and the narration script. A status
file remembers which scripts are done in which language.
"""

import json
import os
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from loguru import logger

from app.config import config
from app.models import const
from app.models.schema import VideoAspect, VideoParams
from app.services import bilingual, script_library, thumbnail, youtube_kit
from app.services import state as sm
from app.services import task as tm
from app.services.thumbnail_ai import prompts as thumbnail_prompts
from app.utils import utils

STOCK_SOURCES = ("pexels", "pixabay")
BATCH_FOLDER = "Batch"

STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_SKIPPED = "skipped"


class BatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class BatchJob:
    entry: script_library.LibraryScript
    language: str  # the language the video is made in

    @property
    def key(self) -> str:
        return status_key(self.entry.id, self.language)

    @property
    def translated(self) -> bool:
        return self.language != self.entry.language


def status_key(script_id: str, language: str) -> str:
    return f"{script_id}:{language}"


def plan_jobs(entries, languages: str) -> list[BatchJob]:
    """languages: "as_written", "other" or "both"."""
    jobs = []
    for entry in entries:
        own, other = entry.language, bilingual.other(entry.language)
        wanted = {"as_written": [own], "other": [other], "both": [own, other]}[languages]
        jobs += [BatchJob(entry, language) for language in wanted]
    return jobs


# ---- status file -------------------------------------------------------------

_status_lock = threading.Lock()


def status_file() -> str:
    return os.path.join(utils.storage_dir("batch", create=True), "status.json")


def load_status() -> dict:
    try:
        with open(status_file(), encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def update_status(key: str, **fields) -> None:
    with _status_lock:
        data = load_status()
        if fields.get("state") is None:
            data.pop(key, None)
        else:
            data[key] = {**data.get(key, {}), **fields, "updated": datetime.now().isoformat(timespec="seconds")}
        temp_path = f"{status_file()}.tmp"
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(temp_path, status_file())


def set_done(script_id: str, language: str, done: bool) -> None:
    update_status(status_key(script_id, language), state=STATE_DONE if done else None)


# ---- one video ---------------------------------------------------------------


def build_params(template: VideoParams, job: BatchJob, script: str, subject: str, video_source: str) -> VideoParams:
    """Video settings for one batch video: the Video Generator's settings, this script, its language voice."""
    params = template.model_copy(deep=True)
    params.video_subject = subject
    params.video_script = script
    params.video_terms = job.entry.keywords or None
    params.video_count = 1
    params.video_source = video_source if video_source in STOCK_SOURCES else STOCK_SOURCES[0]
    params.video_materials = None
    params.material_weights = None
    params.custom_audio_file = None
    params.audio_enabled = True

    profile = bilingual.get_profile(config.ui, job.language)
    template_language = bilingual.TELUGU if str(template.video_language or "").startswith("te") else bilingual.ENGLISH
    if profile:
        params.voice_name = profile["voice_name"]
        params.voice_rate = profile["voice_rate"]
        params.video_language = profile.get("video_language") or bilingual.VIDEO_LANGUAGE[job.language]
        if profile.get("font_name"):
            params.font_name = profile["font_name"]
    elif template_language == job.language and template.voice_name and "no-voice" not in template.voice_name:
        params.video_language = template.video_language or bilingual.VIDEO_LANGUAGE[job.language]
    else:
        raise BatchError(f"no {job.language} voice saved (Video Generator > Language Voices)")
    return params


def _video_folder(job: BatchJob) -> str:
    language = "Telugu" if job.language == bilingual.TELUGU else "English"
    name = script_library.safe_folder_name(f"{job.entry.number:03d} {job.entry.title} ({language})")
    folder = os.path.join(utils.download_dir(create=True), BATCH_FOLDER, name)
    os.makedirs(folder, exist_ok=True)
    return folder


def _upload_kit(job: BatchJob, script: str, subject: str, params: VideoParams) -> str:
    if not job.translated and job.entry.upload_kit:
        return f"# {job.entry.title}\n\n{job.entry.upload_kit}\n"
    kit = youtube_kit.generate_kit(
        script,
        subject=subject,
        language=job.language,
        video_format="shorts" if params.video_aspect in (VideoAspect.portrait, VideoAspect.portrait.value) else "long",
        audience="kids" if job.entry.library.upper().startswith("KIDS") else "general",
    )
    return kit.to_markdown()


def _thumbnail_prompt(job: BatchJob, script: str, params: VideoParams) -> str:
    title = job.entry.thumbnail_title if not job.translated else ""
    title = title or thumbnail.generate_hook_title(script)
    portrait = params.video_aspect in (VideoAspect.portrait, VideoAspect.portrait.value)
    return thumbnail_prompts.build_prompt(
        topic=thumbnail_prompts.summarize_topic(script),
        title=title,
        aspect_ratio="9:16" if portrait else "16:9",
    )


def _save_outputs(job: BatchJob, script: str, subject: str, params: VideoParams, result: dict) -> str:
    folder = _video_folder(job)
    exported = [p for p in result.get("download_videos") or [] if os.path.exists(p)]
    source = exported[0] if exported else (result.get("videos") or [""])[0]
    if not source or not os.path.exists(source):
        raise BatchError("the finished video file is missing")
    target = os.path.join(folder, os.path.basename(source))
    if exported:
        os.replace(source, target)
    else:
        shutil.copy2(source, target)

    files = {
        "script.txt": script + "\n",
        "upload_kit.md": _upload_kit(job, script, subject, params),
        "thumbnail_prompt.txt": _thumbnail_prompt(job, script, params) + "\n",
    }
    for name, content in files.items():
        with open(os.path.join(folder, name), "w", encoding="utf-8") as file:
            file.write(content)
    return folder


# ---- runner ------------------------------------------------------------------


class BatchRunner:
    """One background thread that renders queued jobs in order."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._rows: list[dict] = []

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, jobs: list[BatchJob], template: VideoParams, video_source: str) -> bool:
        with self._lock:
            if self.is_running() or not jobs:
                return False
            self._stop.clear()
            self._rows = [
                {
                    "key": job.key,
                    "title": job.entry.title,
                    "language": job.language,
                    "state": STATE_QUEUED,
                    "task_id": "",
                    "error": "",
                    "folder": "",
                }
                for job in jobs
            ]
            self._thread = threading.Thread(
                target=self._run,
                args=(list(jobs), template.model_copy(deep=True), video_source),
                name="batch-videos",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        """Finishes the current video, then stops."""
        self._stop.set()

    def stopping(self) -> bool:
        return self._stop.is_set() and self.is_running()

    def snapshot(self) -> list[dict]:
        with self._lock:
            rows = [dict(row) for row in self._rows]
        for row in rows:
            if row["state"] == STATE_RUNNING and row["task_id"]:
                task = sm.state.get_task(row["task_id"]) or {}
                row["progress"] = int(task.get("progress") or 0)
        return rows

    def _set(self, index: int, **fields) -> None:
        with self._lock:
            self._rows[index].update(fields)

    def _run(self, jobs: list[BatchJob], template: VideoParams, video_source: str) -> None:
        logger.info(f"batch started: {len(jobs)} videos")
        for index, job in enumerate(jobs):
            if self._stop.is_set():
                for rest in range(index, len(jobs)):
                    self._set(rest, state=STATE_SKIPPED)
                break
            self._run_job(index, job, template, video_source)
        logger.info("batch finished")

    def _run_job(self, index: int, job: BatchJob, template: VideoParams, video_source: str) -> None:
        task_id = str(uuid4())
        self._set(index, state=STATE_RUNNING, task_id=task_id)
        logger.info(f"batch video {index + 1}: {job.entry.id} in {job.language}")
        try:
            script, subject = job.entry.script, job.entry.subject
            if job.translated:
                translated = bilingual.translate(script, subject, job.language)
                script, subject = translated["script"], translated["subject"]
            params = build_params(template, job, script, subject, video_source)
            sm.state.update_task(
                task_id, state=const.TASK_STATE_PROCESSING, progress=0, video_subject=subject
            )
            with config.runtime_config_lock():
                result = tm.start(task_id=task_id, params=params) or {}
            if result.get("state") == const.TASK_STATE_FAILED or not result.get("videos"):
                raise BatchError(result.get("error") or "the video could not be made")
            folder = _save_outputs(job, script, subject, params, result)
        except Exception as exc:
            logger.exception(f"batch video failed: {job.entry.id} ({job.language})")
            self._set(index, state=STATE_FAILED, error=str(exc))
            update_status(job.key, state=STATE_FAILED, error=str(exc), task_id=task_id)
            return
        self._set(index, state=STATE_DONE, folder=folder)
        update_status(job.key, state=STATE_DONE, folder=folder, task_id=task_id)
        logger.success(f"batch video done: {folder}")


runner = BatchRunner()

