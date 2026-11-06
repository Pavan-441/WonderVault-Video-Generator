"""Direct upload to YouTube with the free YouTube Data API v3.

Setup (once): in Google Cloud, enable "YouTube Data API v3", create an OAuth client of
type "Desktop app", download its JSON and add it in the app. Signing in opens Google's
consent page; the token stays in storage/youtube/ on this computer.

Only `requests` is used (no Google client library). Sign-in follows the installed-app
flow with PKCE and a one-shot local redirect server on 127.0.0.1.
"""

import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests
from loguru import logger

from app.services import youtube_kit
from app.utils import utils

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3"
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)
CATEGORY_SCIENCE_TECH = "28"
PRIVACY_OPTIONS = ("private", "unlisted", "public")
CHUNK_BYTES = 8 * 1024 * 1024
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024
SIGN_IN_TIMEOUT_SECONDS = 300
REQUEST_TIMEOUT = 60


class YouTubeError(RuntimeError):
    pass


# ---- local files -------------------------------------------------------------


def _dir() -> str:
    return utils.storage_dir("youtube", create=True)


def client_file() -> str:
    return os.path.join(_dir(), "client.json")


def token_file() -> str:
    return os.path.join(_dir(), "token.json")


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: dict) -> None:
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(data, file)
    os.replace(temp_path, path)


def save_client(raw: bytes | str) -> None:
    """Stores the client id and secret from Google's downloaded OAuth client JSON."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise YouTubeError("this is not a JSON file") from exc
    section = (data.get("installed") or data.get("web")) if isinstance(data, dict) else None
    if not isinstance(section, dict) or not section.get("client_id") or not section.get("client_secret"):
        raise YouTubeError("the file has no OAuth client id and secret")
    _write_json(client_file(), {"client_id": section["client_id"], "client_secret": section["client_secret"]})


def load_client() -> dict:
    return _read_json(client_file())


def is_configured() -> bool:
    return bool(load_client().get("client_id"))


def is_signed_in() -> bool:
    return bool(_read_json(token_file()).get("refresh_token"))


def sign_out() -> None:
    try:
        os.remove(token_file())
    except FileNotFoundError:
        pass


# ---- sign-in -----------------------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _exchange(data: dict) -> dict:
    response = requests.post(TOKEN_URL, data=data, timeout=REQUEST_TIMEOUT)
    payload = response.json() if response.content else {}
    if response.status_code != 200:
        raise YouTubeError(payload.get("error_description") or payload.get("error") or response.text)
    return payload


class SignIn:
    """One Google sign-in: a local page catches the redirect and saves the token."""

    def __init__(self):
        client = load_client()
        if not client.get("client_id"):
            raise YouTubeError("add the OAuth client JSON first")
        self.client = client
        self.port = _free_port()
        self.redirect_uri = f"http://127.0.0.1:{self.port}/"
        self.state = secrets.token_urlsafe(16)
        self.verifier, challenge = _pkce_pair()
        self.url = f"{AUTH_URL}?" + urllib.parse.urlencode(
            {
                "client_id": client["client_id"],
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "state": self.state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        self.status = "waiting"  # waiting -> done | failed
        self.error = ""
        self._server = None

    def start(self) -> "SignIn":
        sign_in = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (http.server API)
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                ok = sign_in._finish(query)
                body = (
                    "Signed in. You can close this tab and go back to the app."
                    if ok
                    else f"Sign-in failed: {sign_in.error}"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def log_message(self, *_args):
                pass

        self._server = http.server.HTTPServer(("127.0.0.1", self.port), Handler)
        self._server.timeout = 1
        threading.Thread(target=self._serve, name="youtube-sign-in", daemon=True).start()
        return self

    def _serve(self):
        deadline = time.monotonic() + SIGN_IN_TIMEOUT_SECONDS
        try:
            while self.status == "waiting" and time.monotonic() < deadline:
                self._server.handle_request()
            if self.status == "waiting":
                self.status, self.error = "failed", "sign-in timed out"
        finally:
            self._server.server_close()

    def _finish(self, query: dict) -> bool:
        if query.get("state", [""])[0] != self.state:
            self.status, self.error = "failed", "the sign-in reply did not match this request"
        elif query.get("error"):
            self.status, self.error = "failed", query["error"][0]
        elif not query.get("code"):
            return False  # e.g. a favicon request; keep waiting
        else:
            try:
                token = _exchange(
                    {
                        "code": query["code"][0],
                        "client_id": self.client["client_id"],
                        "client_secret": self.client["client_secret"],
                        "redirect_uri": self.redirect_uri,
                        "grant_type": "authorization_code",
                        "code_verifier": self.verifier,
                    }
                )
                _save_token(token)
                self.status = "done"
            except (YouTubeError, requests.RequestException, ValueError) as exc:
                self.status, self.error = "failed", str(exc)
        return self.status == "done"


def _save_token(token: dict, previous: dict | None = None) -> dict:
    saved = dict(previous or {})
    saved.update(
        access_token=token["access_token"],
        expires_at=time.time() + int(token.get("expires_in", 3600)) - 60,
    )
    if token.get("refresh_token"):
        saved["refresh_token"] = token["refresh_token"]
    _write_json(token_file(), saved)
    return saved


def access_token() -> str:
    token = _read_json(token_file())
    if not token.get("refresh_token"):
        raise YouTubeError("not signed in to YouTube")
    if token.get("access_token") and token.get("expires_at", 0) > time.time():
        return token["access_token"]
    client = load_client()
    refreshed = _exchange(
        {
            "client_id": client.get("client_id", ""),
            "client_secret": client.get("client_secret", ""),
            "refresh_token": token["refresh_token"],
            "grant_type": "refresh_token",
        }
    )
    return _save_token(refreshed, token)["access_token"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {access_token()}"}


def _api_error(response) -> YouTubeError:
    try:
        error = response.json().get("error", {})
        message = error.get("message") or response.text
    except ValueError:
        message = response.text
    return YouTubeError(f"YouTube said: {message} (HTTP {response.status_code})")


def channel_title() -> str:
    response = requests.get(
        f"{API_URL}/channels",
        params={"part": "snippet", "mine": "true"},
        headers=_headers(),
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise _api_error(response)
    items = response.json().get("items") or []
    return items[0]["snippet"]["title"] if items else ""


# ---- metadata ----------------------------------------------------------------


@dataclass
class UploadMetadata:
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    privacy: str = "private"
    publish_at: datetime | None = None  # scheduled videos stay private until then
    made_for_kids: bool = False
    language: str = ""  # "te" or "en"
    category_id: str = CATEGORY_SCIENCE_TECH

    def validate(self) -> list[str]:
        problems = []
        if not self.title.strip():
            problems.append("the title is empty")
        if len(self.title) > youtube_kit.TITLE_MAX_CHARS:
            problems.append(f"the title is longer than {youtube_kit.TITLE_MAX_CHARS} characters")
        if re.search(r"[<>]", self.title + self.description):
            problems.append("YouTube does not allow < or > in the title or description")
        if len(self.description.encode("utf-8")) > youtube_kit.DESCRIPTION_MAX_CHARS:
            problems.append("the description is longer than 5000 bytes")
        if youtube_kit.tags_character_count(self.tags) > youtube_kit.TAGS_MAX_CHARS:
            problems.append("the tags are longer than 500 characters")
        if self.privacy not in PRIVACY_OPTIONS:
            problems.append("unknown privacy setting")
        if self.publish_at is not None:
            if self.publish_at.tzinfo is None:
                problems.append("the schedule time has no time zone")
            elif self.publish_at <= datetime.now(timezone.utc):
                problems.append("the schedule time is in the past")
        return problems

    def body(self) -> dict:
        snippet = {
            "title": self.title.strip(),
            "description": self.description,
            "tags": self.tags,
            "categoryId": self.category_id,
        }
        if self.language:
            snippet["defaultLanguage"] = self.language
            snippet["defaultAudioLanguage"] = self.language
        status = {
            "privacyStatus": "private" if self.publish_at else self.privacy,
            "selfDeclaredMadeForKids": bool(self.made_for_kids),
        }
        if self.publish_at:
            status["publishAt"] = self.publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return {"snippet": snippet, "status": status}


def parse_kit_markdown(text: str) -> dict:
    """Title, description and tags from an upload kit file (library or YouTube Kit format)."""
    text = text or ""
    result = {"title": "", "description": "", "tags": []}
    title = re.search(r"^- \*\*Title\*\*[^`\n]*`(.+?)`\s*$", text, re.MULTILINE)
    if not title:
        title = re.search(r"^## Title options\s*\n1\. (.+?)(?: \(\d+ chars\))?\s*$", text, re.MULTILINE)
    if title:
        result["title"] = title.group(1).strip()
    description = re.search(r"Description\**:?\s*\n```text\n(.*?)\n```", text, re.DOTALL)
    if description:
        result["description"] = description.group(1).strip()
    tags = re.search(r"^- \*\*Tags\*\*:\s*`(.+?)`\s*$", text, re.MULTILINE)
    if not tags:
        tags = re.search(r"^## Tags[^\n]*\n(.+?)\s*$", text, re.MULTILINE)
    if tags:
        result["tags"] = [t.strip() for t in tags.group(1).split(",") if t.strip()]
    return result


# ---- upload ------------------------------------------------------------------


def upload(video_path: str, metadata: UploadMetadata, progress=None) -> str:
    """Uploads the video and returns its YouTube id. progress(fraction) is called per chunk."""
    problems = metadata.validate()
    if problems:
        raise YouTubeError("; ".join(problems))
    size = os.path.getsize(video_path)
    start = requests.post(
        f"{UPLOAD_URL}/videos",
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            **_headers(),
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4",
        },
        data=json.dumps(metadata.body()),
        timeout=REQUEST_TIMEOUT,
    )
    if start.status_code != 200 or not start.headers.get("Location"):
        raise _api_error(start)
    session_url = start.headers["Location"]

    sent = 0
    with open(video_path, "rb") as file:
        while True:
            chunk = file.read(CHUNK_BYTES)
            end = sent + len(chunk) - 1
            response = requests.put(
                session_url,
                headers={**_headers(), "Content-Range": f"bytes {sent}-{end}/{size}"},
                data=chunk,
                timeout=REQUEST_TIMEOUT * 5,
            )
            if response.status_code in (200, 201):
                if progress:
                    progress(1.0)
                video_id = response.json().get("id")
                if not video_id:
                    raise YouTubeError("YouTube did not return a video id")
                logger.success(f"uploaded to YouTube: {video_id}")
                return video_id
            if response.status_code != 308:
                raise _api_error(response)
            # 308: YouTube kept bytes up to the Range header's end.
            confirmed = response.headers.get("Range", "")
            sent = int(confirmed.rsplit("-", 1)[1]) + 1 if "-" in confirmed else 0
            file.seek(sent)
            if progress:
                progress(sent / size)


def set_thumbnail(video_id: str, image_path: str) -> None:
    if os.path.getsize(image_path) > MAX_THUMBNAIL_BYTES:
        raise YouTubeError("the thumbnail must be 2 MB or smaller")
    content_type = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
    with open(image_path, "rb") as file:
        response = requests.post(
            f"{UPLOAD_URL}/thumbnails/set",
            params={"videoId": video_id},
            headers={**_headers(), "Content-Type": content_type},
            data=file.read(),
            timeout=REQUEST_TIMEOUT,
        )
    if response.status_code != 200:
        raise _api_error(response)


def video_url(video_id: str, short: bool = True) -> str:
    return f"https://youtube.com/shorts/{video_id}" if short else f"https://youtu.be/{video_id}"


def recent_videos(limit: int = 30) -> list[str]:
    """Finished videos in the download folder (including batch folders), newest first."""
    root = utils.download_dir(create=True)
    found = []
    for folder, _dirs, files in os.walk(root):
        found += [os.path.join(folder, name) for name in files if name.lower().endswith(".mp4")]
    return sorted(found, key=os.path.getmtime, reverse=True)[:limit]


def kit_beside(video_path: str) -> dict | None:
    """The upload kit saved next to a batch video, if there is one."""
    kit_path = os.path.join(os.path.dirname(video_path), "upload_kit.md")
    try:
        with open(kit_path, encoding="utf-8") as file:
            parsed = parse_kit_markdown(file.read())
        return parsed if parsed["title"] else None
    except OSError:
        return None
