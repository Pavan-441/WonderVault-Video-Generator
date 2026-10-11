"""AI 原创背景音乐：按文字提示词生成纯音乐，保存到随机 BGM 的用户音乐库。

与 elevenlabs_music.py 的视频配乐不同，这里不需要视频，生成的是可复用的独立曲目。
生成的音乐来自 ElevenLabs 的文字生成接口，不包含任何现成歌曲，因此不会被其他版权方认领。
"""

import datetime
import io
import json
import os
import random
import tempfile
import uuid
from pathlib import Path

import requests
from loguru import logger

from app.services import bgm as bgm_service
from app.services import elevenlabs_music
from app.utils import utils

TEXT_MUSIC_PATH = "/v1/music"
OUTPUT_FORMAT = "mp3_44100_128"
MIN_TRACK_SECONDS = 30
MAX_TRACK_SECONDS = 180
DEFAULT_TRACK_SECONDS = 60
MAX_PROMPT_LENGTH = 1000
# 每首生成记录写入 storage，便于日后在版权申诉时证明曲目由提示词原创生成。
PROVENANCE_LOG_NAME = "ai_music_log.jsonl"

GENRES = (
    "lo-fi hip hop",
    "ambient",
    "acoustic folk",
    "cinematic orchestral",
    "soft jazz",
    "chillhop",
    "minimal electronic",
    "classical piano",
    "upbeat corporate",
    "dreamy synthwave",
    "gentle world music",
    "documentary underscore",
)
MOODS = (
    "calm and reflective",
    "curious and light",
    "uplifting and hopeful",
    "mysterious",
    "warm and nostalgic",
    "energetic but relaxed",
    "dramatic build",
    "peaceful",
)
INSTRUMENTS = (
    "soft piano",
    "acoustic guitar",
    "strings",
    "warm synth pads",
    "marimba and vibraphone",
    "flute",
    "light percussion",
    "harp",
    "bass and brushed drums",
)
TEMPOS = ("slow tempo", "medium tempo", "steady mid-tempo")
# 旁白视频的背景音乐必须“让位”于人声，因此固定加上这些约束。
BACKGROUND_CONSTRAINTS = (
    "instrumental only, no vocals",
    "background music that stays under narration",
    "no sudden loud sections",
)


class AiMusicError(RuntimeError):
    """AI 音乐生成失败的可读原因。"""


def build_random_prompt(rng: random.Random | None = None) -> str:
    """随机组合风格、情绪、乐器和节奏，生成一条适合旁白视频的纯音乐提示词。"""
    rng = rng or random.Random()
    parts = [
        rng.choice(GENRES),
        rng.choice(MOODS),
        rng.choice(INSTRUMENTS),
        rng.choice(TEMPOS),
        *BACKGROUND_CONSTRAINTS,
    ]
    return ", ".join(parts)


def clamp_track_seconds(seconds: int) -> int:
    return max(MIN_TRACK_SECONDS, min(MAX_TRACK_SECONDS, int(seconds)))


def _validate_prompt(prompt: str) -> str:
    cleaned = (prompt or "").strip()
    if not cleaned:
        raise AiMusicError("Music prompt is empty")
    if len(cleaned) > MAX_PROMPT_LENGTH:
        raise AiMusicError(f"Music prompt exceeds {MAX_PROMPT_LENGTH} characters")
    return cleaned


def generate_track(prompt: str, seconds: int = DEFAULT_TRACK_SECONDS) -> bytes:
    """调用 ElevenLabs 文字生成接口，返回已通过 FFmpeg 校验的 MP3 字节。"""
    prompt = _validate_prompt(prompt)
    seconds = clamp_track_seconds(seconds)

    if not elevenlabs_music.is_enabled():
        raise AiMusicError("ElevenLabs API key is not configured")

    base_url = elevenlabs_music._base_url()
    payload = {
        "prompt": prompt,
        "music_length_ms": seconds * 1000,
        "model_id": elevenlabs_music._model_id(),
    }
    descriptor, temp_path = tempfile.mkstemp(prefix=".ai-music-", suffix=".mp3")
    os.close(descriptor)
    try:
        logger.info(f"requesting AI background music: seconds={seconds}")
        try:
            with requests.post(
                f"{base_url}{TEXT_MUSIC_PATH}",
                headers={"xi-api-key": elevenlabs_music.get_api_key()},
                params={"output_format": OUTPUT_FORMAT},
                json=payload,
                stream=True,
                timeout=elevenlabs_music._request_timeout(),
            ) as response:
                if not response.ok:
                    raise AiMusicError(
                        "ElevenLabs music generation failed "
                        f"({response.status_code}): "
                        f"{elevenlabs_music._safe_response_error(response)}"
                    )
                elevenlabs_music._stream_audio(response, temp_path)
        except requests.RequestException as exc:
            raise AiMusicError(f"failed to request ElevenLabs music: {exc}") from exc
        except elevenlabs_music.ElevenLabsMusicError as exc:
            raise AiMusicError(str(exc)) from exc

        try:
            bgm_service.validate_audio_file(temp_path, timeout_seconds=120)
        except (bgm_service.BgmUploadError, bgm_service.BgmServiceError) as exc:
            raise AiMusicError("Generated audio could not be decoded") from exc

        with open(temp_path, "rb") as audio:
            return audio.read()
    finally:
        elevenlabs_music._remove_file(temp_path)


def save_to_bgm_library(audio_bytes: bytes, prompt: str, seconds: int) -> str:
    """把生成的曲目放入随机 BGM 使用的用户音乐库，并记录提示词作为版权证明。"""
    if not audio_bytes:
        raise AiMusicError("No audio to save")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"ai-music-{stamp}-{uuid.uuid4().hex[:8]}.mp3"
    saved_path = bgm_service.save_bgm_upload(filename, io.BytesIO(audio_bytes))
    _append_provenance(saved_path, prompt, seconds)
    return saved_path


def _append_provenance(saved_path: str, prompt: str, seconds: int) -> None:
    log_path = os.path.join(utils.storage_dir(create=True), PROVENANCE_LOG_NAME)
    record = {
        "file": Path(saved_path).name,
        "prompt": prompt,
        "seconds": seconds,
        "model": elevenlabs_music._model_id(),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        # 日志只是证明材料，写入失败不应让已经保存好的音乐报错。
        logger.warning(f"failed to write AI music provenance log: {exc}")
