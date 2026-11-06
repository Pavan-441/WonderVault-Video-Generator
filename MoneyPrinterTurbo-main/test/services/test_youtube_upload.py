"""Direct YouTube upload: sign-in, metadata and resumable upload, with Google faked."""

import json
import os
import time
import urllib.parse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services import state as sm, version_checker, voice, youtube_kit, youtube_upload as yu

WEBUI_MAIN = Path(__file__).resolve().parents[2] / "webui" / "Main.py"
CLIENT = {"installed": {"client_id": "id-123", "client_secret": "secret-456"}}


@pytest.fixture
def store(tmp_path):
    with patch.object(yu, "_dir", return_value=str(tmp_path)):
        yield tmp_path


def _response(status=200, body=None, headers=None):
    return SimpleNamespace(
        status_code=status,
        headers=headers or {},
        content=b"x",
        text=json.dumps(body or {}),
        json=lambda: body or {},
    )


def test_client_file_is_validated_and_saved(store):
    with pytest.raises(yu.YouTubeError):
        yu.save_client(b"not json")
    with pytest.raises(yu.YouTubeError):
        yu.save_client(json.dumps({"installed": {"client_id": "x"}}))
    yu.save_client(json.dumps(CLIENT).encode())
    assert yu.load_client() == {"client_id": "id-123", "client_secret": "secret-456"}
    assert yu.is_configured() and not yu.is_signed_in()


def test_sign_in_catches_the_redirect_and_saves_the_token(store):
    yu.save_client(json.dumps(CLIENT))
    sign_in = yu.SignIn()
    query = urllib.parse.parse_qs(urllib.parse.urlparse(sign_in.url).query)
    assert query["access_type"] == ["offline"] and query["code_challenge_method"] == ["S256"]
    assert "youtube.upload" in query["scope"][0]

    token = {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
    real_get = requests.get
    with patch.object(yu.requests, "post", return_value=_response(200, token)) as post:
        sign_in.start()
        page = real_get(f"{sign_in.redirect_uri}?code=abc&state={sign_in.state}", timeout=10)
    assert "Signed in" in page.text and sign_in.status == "done"
    assert post.call_args.kwargs["data"]["code_verifier"] == sign_in.verifier
    assert yu.is_signed_in() and yu.access_token() == "at"


def test_sign_in_rejects_a_reply_for_another_request(store):
    yu.save_client(json.dumps(CLIENT))
    sign_in = yu.SignIn().start()
    page = requests.get(f"{sign_in.redirect_uri}?code=abc&state=wrong", timeout=10)
    assert "failed" in page.text and sign_in.status == "failed"
    assert not yu.is_signed_in()


def test_expired_access_token_is_refreshed(store):
    yu.save_client(json.dumps(CLIENT))
    (store / "token.json").write_text(
        json.dumps({"access_token": "old", "refresh_token": "rt", "expires_at": time.time() - 5})
    )
    with patch.object(yu.requests, "post", return_value=_response(200, {"access_token": "new", "expires_in": 3600})) as post:
        assert yu.access_token() == "new"
    assert post.call_args.kwargs["data"]["grant_type"] == "refresh_token"
    assert json.loads((store / "token.json").read_text())["refresh_token"] == "rt"


def test_not_signed_in_is_a_clear_error(store):
    with pytest.raises(yu.YouTubeError, match="not signed in"):
        yu.access_token()


def test_metadata_rules_and_schedule_body():
    when = datetime.now(timezone.utc) + timedelta(days=1)
    meta = yu.UploadMetadata("Black Hole 🕳️", "desc", ["space"], "public", when, True, "te")
    assert meta.validate() == []
    body = meta.body()
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"].endswith("Z") and body["status"]["selfDeclaredMadeForKids"]
    assert body["snippet"]["categoryId"] == "28" and body["snippet"]["defaultAudioLanguage"] == "te"

    bad = yu.UploadMetadata("x" * 101 + "<", "", ["t" * 600], "secret", datetime.now(timezone.utc) - timedelta(1))
    problems = " ".join(bad.validate())
    for text in ("longer than 100", "< or >", "500 characters", "unknown privacy", "in the past"):
        assert text in problems


def test_parse_library_kit_and_generated_kit():
    library = (
        "- **Title** (46 chars): `What Happens If You Fall Into a Black Hole? 🕳️`\n"
        "- **Description**:\n```text\nLine one.\n\n#Shorts\n```\n"
        "- **Tags**: `black hole, space facts, wonder vault`\n"
    )
    assert yu.parse_kit_markdown(library) == {
        "title": "What Happens If You Fall Into a Black Hole? 🕳️",
        "description": "Line one.\n\n#Shorts",
        "tags": ["black hole", "space facts", "wonder vault"],
    }
    kit = youtube_kit.YouTubeKit(
        titles=["Title A 🌊", "Title B"],
        description="Desc",
        tags=["a", "b c"],
        keywords=[],
        hashtags=[],
        pinned_comment="",
        playlist="",
        language="english",
        video_format="shorts",
    )
    assert yu.parse_kit_markdown(kit.to_markdown()) == {
        "title": "Title A 🌊",
        "description": "Desc",
        "tags": ["a", "b c"],
    }


def test_resumable_upload_resends_from_what_youtube_kept(store, tmp_path):
    (store / "token.json").write_text(
        json.dumps({"access_token": "at", "refresh_token": "rt", "expires_at": time.time() + 600})
    )
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0123456789")
    put_ranges = []

    def fake_put(url, headers, data, timeout):
        put_ranges.append(headers["Content-Range"])
        if len(put_ranges) == 1:
            return _response(308, headers={"Range": "bytes=0-1"})  # kept only 2 of 4 bytes
        if headers["Content-Range"].startswith("bytes 6-"):
            return _response(200, {"id": "vid42"})
        return _response(308, headers={"Range": f"bytes=0-{int(headers['Content-Range'].split('-')[1].split('/')[0])}"})

    progress = []
    with patch.object(yu, "CHUNK_BYTES", 4), patch.object(
        yu.requests, "post", return_value=_response(200, headers={"Location": "https://upload/session"})
    ) as post, patch.object(yu.requests, "put", side_effect=fake_put):
        video_id = yu.upload(str(video), yu.UploadMetadata("T"), progress.append)

    assert video_id == "vid42"
    assert put_ranges == ["bytes 0-3/10", "bytes 2-5/10", "bytes 6-9/10"]
    assert json.loads(post.call_args.kwargs["data"])["status"]["privacyStatus"] == "private"
    assert progress[-1] == 1.0


def test_upload_refuses_invalid_metadata(store, tmp_path):
    with pytest.raises(yu.YouTubeError, match="title is empty"):
        yu.upload(str(tmp_path / "v.mp4"), yu.UploadMetadata(""))


def test_api_errors_are_readable(store):
    (store / "token.json").write_text(
        json.dumps({"access_token": "at", "refresh_token": "rt", "expires_at": time.time() + 600})
    )
    error = _response(403, {"error": {"message": "quotaExceeded"}})
    with patch.object(yu.requests, "get", return_value=error), pytest.raises(yu.YouTubeError, match="quotaExceeded"):
        yu.channel_title()


def test_recent_videos_and_kit_beside(tmp_path):
    batch_dir = tmp_path / "Batch" / "001 Sky"
    batch_dir.mkdir(parents=True)
    old, new = tmp_path / "old.mp4", batch_dir / "new.mp4"
    old.write_bytes(b"1")
    new.write_bytes(b"2")
    os.utime(old, (1, 1))
    (batch_dir / "upload_kit.md").write_text("- **Title** (3 chars): `Sky`\n", encoding="utf-8")
    with patch.object(yu.utils, "download_dir", return_value=str(tmp_path)):
        assert yu.recent_videos() == [str(new), str(old)]
    assert yu.kit_beside(str(new))["title"] == "Sky"
    assert yu.kit_beside(str(old)) is None


@pytest.fixture
def run_webui(tmp_path):
    with ExitStack() as stack:

        def start(session=None):
            stack.enter_context(patch.object(config, "app", {"llm_provider": "openai"}))
            stack.enter_context(patch.object(config, "ui", {"language": "en"}))
            stack.enter_context(patch.object(config, "try_save_config", return_value=True))
            stack.enter_context(patch.object(sm.state, "get_all_tasks", return_value=([], 0)))
            stack.enter_context(patch.object(voice, "get_all_azure_voices", return_value=["en-US-Jenny"]))
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

        yield start, stack


def test_page_shows_setup_steps_before_a_client_is_added(run_webui, store):
    start, _ = run_webui
    app = start()
    assert any("Enable **YouTube Data API v3**" in m.value or "YouTube Data API v3" in m.value for m in app.markdown)


def test_page_prefills_a_batch_video_and_uploads_it(run_webui, store, tmp_path):
    start, stack = run_webui
    yu.save_client(json.dumps(CLIENT))
    (store / "token.json").write_text(
        json.dumps({"access_token": "at", "refresh_token": "rt", "expires_at": time.time() + 600})
    )
    downloads = tmp_path / "download"
    folder = downloads / "Batch" / "002 Sky (English)"
    folder.mkdir(parents=True)
    (folder / "Sky.mp4").write_bytes(b"video")
    (folder / "upload_kit.md").write_text(
        "- **Title** (20 chars): `Why Is the Sky Blue? 🌈`\n- **Description**:\n```text\nBlue light.\n```\n"
        "- **Tags**: `sky, science`\n",
        encoding="utf-8",
    )
    stack.enter_context(patch.object(yu.utils, "download_dir", return_value=str(downloads)))
    stack.enter_context(patch.object(yu, "channel_title", return_value="Wonder Vault"))
    upload = stack.enter_context(patch.object(yu, "upload", return_value="vid9"))
    app = start()

    assert any("Signed in as Wonder Vault" in c.value for c in app.caption)
    assert app.text_input(key="yt_upload_title").value == "Why Is the Sky Blue? 🌈"
    assert app.text_area(key="yt_upload_description").value == "Blue light."
    assert app.text_area(key="yt_upload_tags").value == "sky, science"

    app.button(key="yt_upload_btn").click().run()
    assert not app.exception
    path, metadata = upload.call_args.args[:2]
    assert path.endswith("Sky.mp4") and metadata.privacy == "private" and metadata.tags == ["sky", "science"]
    assert any("Uploaded: https://youtube.com/shorts/vid9" in s.value for s in app.success)
