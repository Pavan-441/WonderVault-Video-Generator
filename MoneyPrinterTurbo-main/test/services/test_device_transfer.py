"""Moving the app to another computer with one backup file."""

import io
import json
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
import toml
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services import batch, bgm, device_transfer as dt, pronunciation, state as sm, version_checker, voice, youtube_upload

WEBUI_MAIN = Path(__file__).resolve().parents[2] / "webui" / "Main.py"

SETTINGS = {
    "listen_port": 8080,
    "app": {
        "llm_provider": "gemini",
        "gemini_api_key": "AIza-secret",
        "pexels_api_keys": ["p1", "p2"],
        "ffmpeg_path": "C:/old/ffmpeg.exe",
        "video_source": "local",
    },
    "azure": {"speech_key": "az", "speech_region": "eastus"},
    "ui": {
        "voice_name": "gemini:Achernar-Soft",
        "youtube_kit_channel": "@WonderVault-W",
        "language_profiles": {"telugu": {"voice_name": "gemini:Aoede", "voice_rate": 1.1}},
    },
}


def test_strip_secrets_empties_keys_but_keeps_everything_else():
    stripped = dt.strip_secrets(SETTINGS)
    assert stripped["app"]["gemini_api_key"] == "" and stripped["app"]["pexels_api_keys"] == []
    assert stripped["azure"] == {"speech_key": "", "speech_region": "eastus"}
    assert stripped["ui"] == SETTINGS["ui"]
    assert stripped["app"]["video_source"] == "local"


@pytest.fixture
def computer(tmp_path):
    """One computer's storage: music, pronunciation, batch status, YouTube files."""

    def make(name):
        root = tmp_path / name
        (root / "bgm").mkdir(parents=True)
        (root / "youtube").mkdir()
        return root

    def use(stack, root):
        stack.enter_context(patch.object(bgm, "uploaded_bgm_dir", return_value=str(root / "bgm")))
        stack.enter_context(
            patch.object(bgm, "list_uploaded_bgm_files", side_effect=lambda: sorted(str(p) for p in (root / "bgm").glob("*.mp3")))
        )
        stack.enter_context(patch.object(pronunciation, "rules_file", return_value=str(root / "pronunciation.json")))
        stack.enter_context(patch.object(batch, "status_file", return_value=str(root / "status.json")))
        stack.enter_context(patch.object(youtube_upload, "_dir", return_value=str(root / "youtube")))
        stack.enter_context(patch.object(dt.utils, "root_dir", return_value=str(root)))
        stack.enter_context(patch.object(dt.utils, "storage_dir", side_effect=lambda sub="", create=False: str(root / sub)))
        pronunciation._cache.update(mtime=None, rules=[])

    yield make, use
    pronunciation._cache.update(mtime=None, rules=[])


def _old_computer(make, use, stack):
    root = make("old")
    use(stack, root)
    (root / "bgm" / "track1.mp3").write_bytes(b"ID3music")
    (root / "bgm" / ".bgm-upload-temp.mp3").write_bytes(b"")
    pronunciation.save_rules([{"word": "DNA", "say": "డీ ఎన్ ఏ", "scope": "telugu"}])
    batch.set_done("LIB#001", "telugu", True)
    youtube_upload.save_client(json.dumps({"installed": {"client_id": "cid", "client_secret": "cs"}}))
    stack.enter_context(patch.object(dt.config, "as_dict", return_value=SETTINGS))
    return root


def test_export_without_keys(computer, tmp_path):
    make, use = computer
    with ExitStack() as stack:
        _old_computer(make, use, stack)
        target = tmp_path / "backup.zip"
        manifest = dt.export_backup(str(target), include_keys=False)

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        saved = toml.loads(archive.read("config.toml").decode())
    assert names == {"manifest.json", "config.toml", "pronunciation.json", "batch_status.json", "music/track1.mp3"}
    assert manifest["music"] == ["track1.mp3"] and not manifest["includes_keys"]
    assert saved["app"]["gemini_api_key"] == "" and saved["ui"]["language_profiles"]["telugu"]["voice_name"] == "gemini:Aoede"


def test_export_with_keys_and_without_music(computer, tmp_path):
    make, use = computer
    with ExitStack() as stack:
        _old_computer(make, use, stack)
        target = tmp_path / "backup.zip"
        dt.export_backup(str(target), include_keys=True, include_music=False)
    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        saved = toml.loads(archive.read("config.toml").decode())
    assert "youtube_client.json" in names and not any(n.startswith("music/") for n in names)
    assert saved["app"]["gemini_api_key"] == "AIza-secret"


def test_import_on_a_new_computer_merges_without_losing_local_keys_or_paths(computer, tmp_path):
    make, use = computer
    backup = tmp_path / "backup.zip"
    with ExitStack() as stack:
        _old_computer(make, use, stack)
        dt.export_backup(str(backup), include_keys=False)

    new_sections = {
        "app": {"gemini_api_key": "NEW-LOCAL-KEY", "ffmpeg_path": "D:/new/ffmpeg.exe", "video_source": "pexels"},
        "azure": {},
        "ui": {"voice_name": "en-US-Jenny"},
    }
    with ExitStack() as stack:
        root = make("new")
        use(stack, root)
        (root / "bgm" / "track1.mp3").write_bytes(b"already here")
        stack.enter_context(patch.object(dt.config, "sections", return_value=new_sections))
        save = stack.enter_context(patch.object(dt.config, "save_config"))
        summary = dt.import_backup(backup.read_bytes())

        assert new_sections["app"]["gemini_api_key"] == "NEW-LOCAL-KEY"
        assert new_sections["app"]["ffmpeg_path"] == "D:/new/ffmpeg.exe"
        assert new_sections["app"]["video_source"] == "local"
        assert new_sections["ui"]["voice_name"] == "gemini:Achernar-Soft"
        assert new_sections["ui"]["language_profiles"]["telugu"]["voice_rate"] == 1.1
        save.assert_called_once()
        assert summary["music"] == 0 and summary["music_skipped"] == 1
        assert (root / "bgm" / "track1.mp3").read_bytes() == b"already here"
        assert [r.word for r in pronunciation.load_rules()] == ["DNA"]
        assert batch.load_status()["LIB#001:telugu"]["state"] == "done"
        assert not youtube_upload.is_configured()  # keys were not exported


def _zip(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


MANIFEST = json.dumps({"schema": dt.SCHEMA, "version": dt.VERSION})


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not a zip", "not a backup"),
        (_zip({"manifest.json": MANIFEST, "../config.toml": "x"}), "unexpected file"),
        (_zip({"manifest.json": MANIFEST, "music/../../evil.mp3": "x"}), "unexpected file"),
        (_zip({"manifest.json": MANIFEST, "music/run.exe": "x"}), "unexpected file"),
        (_zip({"config.toml": ""}), "no manifest"),
        (_zip({"manifest.json": json.dumps({"schema": "other", "version": 1})}), "another app"),
    ],
)
def test_unsafe_or_foreign_files_are_refused(data, message):
    with pytest.raises(dt.BackupError, match=message):
        dt.import_backup(data)


def test_settings_dialog_has_the_backup_tab(tmp_path):
    with ExitStack() as stack:
        stack.enter_context(patch.object(config, "app", {"llm_provider": "openai"}))
        stack.enter_context(patch.object(config, "ui", {"language": "en"}))
        stack.enter_context(patch.object(config, "try_save_config", return_value=True))
        stack.enter_context(patch.object(sm.state, "get_all_tasks", return_value=([], 0)))
        stack.enter_context(patch.object(voice, "get_all_azure_voices", return_value=["en-US-Jenny"]))
        stack.enter_context(patch.object(bgm, "list_uploaded_bgm_files", return_value=[]))
        stack.enter_context(
            patch.object(
                version_checker,
                "poll_available_update",
                return_value=version_checker.UpdateCheckSnapshot(complete=True),
            )
        )
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=60)
        app.session_state["ui_language"] = "en"
        app.session_state["settings_dialog_open"] = True
        app.run()
        assert not app.exception
        assert any(tab.label == "Move to Another Computer" for tab in app.tabs)
        assert app.checkbox(key="device_backup_music").label == "Include the music library (0 tracks, 0 MB)"
        assert app.checkbox(key="device_backup_keys").value is False
