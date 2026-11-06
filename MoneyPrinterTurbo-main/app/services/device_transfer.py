"""Move the app to another computer: one backup file out, one import in.

The backup is a zip with the settings (config.toml), the music library (storage/bgm),
pronunciation fixes, saved language voices (part of the settings), batch progress and,
optionally, API keys and the YouTube OAuth client. YouTube sign-in tokens never leave
this computer: sign in again on the new one.
"""

import io
import json
import os
import re
import shutil
import zipfile
from datetime import datetime

import toml
from loguru import logger

from app.config import config
from app.services import batch, bgm, pronunciation, youtube_upload
from app.utils import utils

SCHEMA = "wondervault.device-backup"
VERSION = 1
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024

CONFIG_NAME = "config.toml"
PRONUNCIATION_NAME = "pronunciation.json"
BATCH_STATUS_NAME = "batch_status.json"
YOUTUBE_CLIENT_NAME = "youtube_client.json"
MUSIC_PREFIX = "music/"

_SECRET_KEY = re.compile(r"(^|_)(api_?keys?|keys?|secret|token|password|passwd)($|_)")
# Paths that belong to one computer; the new computer keeps its own.
_LOCAL_ONLY = {("app", "ffmpeg_path"), ("app", "imagemagick_path"), ("app", "download_directory")}


class BackupError(ValueError):
    pass


def is_secret(key: str) -> bool:
    return bool(_SECRET_KEY.search(str(key).lower()))


def _blank(value):
    return [] if isinstance(value, list) else "" if isinstance(value, str) else value


def strip_secrets(data):
    """A copy of the settings with every key, secret, token or password emptied."""
    if not isinstance(data, dict):
        return data
    return {
        key: _blank(value) if is_secret(key) and not isinstance(value, dict) else strip_secrets(value)
        for key, value in data.items()
    }


def _music_files() -> list[str]:
    return [path for path in bgm.list_uploaded_bgm_files() if os.path.getsize(path) > 0]


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as file:
            return file.read()
    except OSError:
        return None


def export_backup(target_path: str, include_keys: bool = False, include_music: bool = True) -> dict:
    """Writes the backup zip and returns its manifest."""
    settings = config.as_dict()
    if not include_keys:
        settings = strip_secrets(settings)
    files = {CONFIG_NAME: toml.dumps(settings).encode("utf-8")}
    for name, path in (
        (PRONUNCIATION_NAME, pronunciation.rules_file()),
        (BATCH_STATUS_NAME, batch.status_file()),
    ):
        data = _read(path)
        if data:
            files[name] = data
    if include_keys and youtube_upload.is_configured():
        files[YOUTUBE_CLIENT_NAME] = _read(youtube_upload.client_file())

    music = _music_files() if include_music else []
    manifest = {
        "schema": SCHEMA,
        "version": VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "app_version": config.project_version,
        "includes_keys": include_keys,
        "music": [os.path.basename(path) for path in music],
        "files": sorted(files),
    }
    with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, data in files.items():
            archive.writestr(name, data)
        for path in music:
            # Music is already compressed; storing it is faster and just as small.
            archive.write(path, MUSIC_PREFIX + os.path.basename(path), compress_type=zipfile.ZIP_STORED)
    logger.info(f"device backup written: {target_path} ({len(music)} music tracks)")
    return manifest


def _allowed_member(name: str) -> bool:
    if name in ("manifest.json", CONFIG_NAME, PRONUNCIATION_NAME, BATCH_STATUS_NAME, YOUTUBE_CLIENT_NAME):
        return True
    if name.startswith(MUSIC_PREFIX):
        track = name[len(MUSIC_PREFIX) :]
        return (
            track == os.path.basename(track)
            and not track.startswith(".")
            and os.path.splitext(track)[1].lower() in bgm.SUPPORTED_BGM_EXTENSIONS
        )
    return False


def _open_backup(data: bytes) -> tuple[zipfile.ZipFile, dict]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BackupError("this is not a backup file") from exc
    total = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        if not _allowed_member(info.filename):
            raise BackupError(f"unexpected file in the backup: {info.filename}")
        if info.file_size > MAX_FILE_BYTES:
            raise BackupError(f"{info.filename} is too large")
        total += info.file_size
    if total > MAX_TOTAL_BYTES:
        raise BackupError("the backup is too large")
    try:
        manifest = json.loads(archive.read("manifest.json"))
    except (KeyError, ValueError) as exc:
        raise BackupError("the backup has no manifest") from exc
    if manifest.get("schema") != SCHEMA or manifest.get("version") != VERSION:
        raise BackupError("this backup was made by another app or version")
    return archive, manifest


def _merge_settings(imported: dict) -> int:
    """Copies imported settings into the live config; keeps local secrets the backup left empty."""
    changed = 0
    live = config.sections()
    for name, value in imported.items():
        if not isinstance(value, dict):
            continue  # top-level values (ports, project name) stay as they are
        section = live.get(name)
        if section is None:
            section = config._cfg.setdefault(name, {})
        for key, new_value in value.items():
            if (name, key) in _LOCAL_ONLY and section.get(key):
                continue
            if is_secret(key) and not new_value and section.get(key):
                continue
            if section.get(key) != new_value:
                section[key] = new_value
                changed += 1
    return changed


def import_backup(data: bytes) -> dict:
    """Applies a backup to this computer and returns what changed."""
    archive, manifest = _open_backup(data)
    names = set(archive.namelist())
    summary = {"settings": 0, "music": 0, "music_skipped": 0, "pronunciation": 0, "batch": 0, "youtube_client": False}

    with archive:
        if CONFIG_NAME in names:
            imported = toml.loads(archive.read(CONFIG_NAME).decode("utf-8"))
            config_path = os.path.join(utils.root_dir(), "config.toml")
            if os.path.exists(config_path):
                backup_dir = utils.storage_dir("backups", create=True)
                shutil.copy2(
                    config_path,
                    os.path.join(backup_dir, f"config-{datetime.now():%Y%m%d-%H%M%S}.toml"),
                )
            with config.try_runtime_config_lock() as acquired:
                if not acquired:
                    raise BackupError("a video is being made; import the backup after it finishes")
                summary["settings"] = _merge_settings(imported)
                config.save_config()

        music_dir = bgm.uploaded_bgm_dir(create=True)
        for name in sorted(n for n in names if n.startswith(MUSIC_PREFIX)):
            target = os.path.join(music_dir, name[len(MUSIC_PREFIX) :])
            if os.path.exists(target):
                summary["music_skipped"] += 1
                continue
            with archive.open(name) as source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)
            summary["music"] += 1

        if PRONUNCIATION_NAME in names:
            incoming = json.loads(archive.read(PRONUNCIATION_NAME)).get("rules") or []
            merged = pronunciation.save_rules(list(pronunciation.load_rules()) + list(incoming))
            summary["pronunciation"] = len(merged)

        if BATCH_STATUS_NAME in names:
            incoming = json.loads(archive.read(BATCH_STATUS_NAME))
            local = batch.load_status()
            added = {k: v for k, v in incoming.items() if k not in local}
            for key, entry in added.items():
                batch.update_status(key, **{k: v for k, v in entry.items() if k != "updated"})
            summary["batch"] = len(added)

        if YOUTUBE_CLIENT_NAME in names and not youtube_upload.is_configured():
            youtube_upload.save_client(json.dumps({"installed": json.loads(archive.read(YOUTUBE_CLIENT_NAME))}))
            summary["youtube_client"] = True

    summary["from_version"] = manifest.get("app_version", "")
    summary["included_keys"] = bool(manifest.get("includes_keys"))
    logger.info(f"device backup imported: {summary}")
    return summary
