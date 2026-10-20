"""视频/音频语音转文字：ffmpeg 抽取 16kHz 单声道 WAV，再用 Whisper 转写为文本。

输出的文本用于「音频生成与下载」里的旁白文案，因此保留标点，不做字幕式切分。
"""

import glob
import os
import subprocess
import sys
import tempfile

from loguru import logger

from app.services import subtitle
from app.utils import utils

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None

# GPU 上使用 float16 权重与 int8 计算，显存占用远低于 float16，适合 4GB 显存的显卡。
GPU_COMPUTE_TYPE = "int8_float16"
_gpu_model = None
_gpu_unavailable = False

LANGUAGE_AUTO = "auto"
LANGUAGE_TELUGU_ENGLISH = "te_en"
LANGUAGE_TELUGU = "te"
LANGUAGE_ENGLISH = "en"
LANGUAGE_MODES = (
    LANGUAGE_AUTO,
    LANGUAGE_TELUGU_ENGLISH,
    LANGUAGE_TELUGU,
    LANGUAGE_ENGLISH,
)

# 混合语言不能强制单一语言，否则英文单词会被转成泰卢固文字。这里让 Whisper 自动检测，
# 并用两种文字的提示词引导它保留英文单词的拉丁字母写法。
_MIXED_LANGUAGE_PROMPT = (
    "ఈ వీడియోలో తెలుగు మరియు English కలిసి మాట్లాడతారు. "
    "Example: ఇది చాలా interesting fact, let's learn together."
)

# language_mode -> (whisper language code 或 None 表示自动检测, decoder prompt 或 None)
_LANGUAGE_SETTINGS = {
    LANGUAGE_AUTO: (None, None),
    LANGUAGE_TELUGU_ENGLISH: (None, _MIXED_LANGUAGE_PROMPT),
    LANGUAGE_TELUGU: ("te", None),
    LANGUAGE_ENGLISH: ("en", None),
}

FFMPEG_TIMEOUT_SECONDS = 600
# 单个源文件的大小上限；界面上传控件与服务层共用这一个值。
MAX_VIDEO_TEXT_UPLOAD_BYTES = 500 * 1024 * 1024
MAX_VIDEO_TEXT_UPLOAD_MB = MAX_VIDEO_TEXT_UPLOAD_BYTES // (1024 * 1024)
# 上传页面允许的文件类型（视频与纯音频都可以，ffmpeg 统一抽取音轨）。
VIDEO_TEXT_SOURCE_TYPES = (
    "mp4",
    "mov",
    "mkv",
    "webm",
    "avi",
    "m4v",
    "mp3",
    "wav",
    "m4a",
    "aac",
    "flac",
    "ogg",
)


class VideoTranscriptError(Exception):
    """转写失败的可读原因，界面直接展示给用户。"""


def _extract_audio(media_path: str, wav_path: str) -> None:
    """抽取音轨为 16kHz 单声道 WAV，Whisper 的标准输入格式。"""
    try:
        result = subprocess.run(
            [
                utils.get_ffmpeg_binary(),
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                media_path,
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                wav_path,
            ],
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoTranscriptError("Audio extraction timed out") from exc

    if result.returncode != 0 or not os.path.isfile(wav_path) or os.path.getsize(wav_path) == 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        logger.error(f"ffmpeg audio extraction failed: {stderr}")
        raise VideoTranscriptError("No readable audio track was found in this file")


def _register_cuda_dll_dirs() -> None:
    """Windows 上让 CTranslate2 找到 pip 安装的 CUDA 运行库（cuBLAS、cuDNN）。"""
    if not hasattr(os, "add_dll_directory"):
        return
    for entry in sys.path:
        for bin_dir in glob.glob(os.path.join(entry, "nvidia", "*", "bin")):
            os.add_dll_directory(bin_dir)
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


def _get_gpu_model():
    """加载 GPU 版 Whisper 模型并缓存；CUDA 不可用时返回 None，之后不再重试。"""
    global _gpu_model, _gpu_unavailable
    if _gpu_unavailable:
        return None
    if _gpu_model is None:
        try:
            _register_cuda_dll_dirs()
            _gpu_model = WhisperModel(
                subtitle.resolve_model_path(),
                device="cuda",
                compute_type=GPU_COMPUTE_TYPE,
            )
            logger.info("video text transcription uses the GPU")
        except Exception as exc:
            logger.warning(f"GPU transcription unavailable, falling back to CPU: {exc}")
            _gpu_unavailable = True
            return None
    return _gpu_model


def _transcribe(wav_path: str, language: str | None, prompt: str | None):
    """优先用 GPU 转写；GPU 运行失败时自动改用 CPU 重试，结果始终返回分段列表。"""
    global _gpu_model, _gpu_unavailable
    options = dict(
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        language=language,
        # 关闭上下文续写：否则长音频中容易重复同一句，导致文字循环。
        condition_on_previous_text=False,
        **({"initial_prompt": prompt} if prompt else {}),
    )

    gpu_model = _get_gpu_model()
    if gpu_model is not None:
        try:
            segments, info = gpu_model.transcribe(wav_path, **options)
            return list(segments), info
        except RuntimeError as exc:
            logger.warning(f"GPU transcription failed, retrying on CPU: {exc}")
            _gpu_model = None
            _gpu_unavailable = True

    cpu_model = subtitle.get_model()
    if cpu_model is None:
        raise VideoTranscriptError("Speech recognition is unavailable. Check the Whisper setup.")
    segments, info = cpu_model.transcribe(wav_path, **options)
    return list(segments), info


def extract_text(media_bytes: bytes, filename: str, language_mode: str = LANGUAGE_AUTO) -> str:
    """把视频或音频里的语音转写为一段文本；没有可识别语音时返回空字符串。"""
    if not isinstance(media_bytes, bytes) or not media_bytes:
        raise VideoTranscriptError("The uploaded file is empty")
    if len(media_bytes) > MAX_VIDEO_TEXT_UPLOAD_BYTES:
        raise VideoTranscriptError(
            f"The file is larger than the {MAX_VIDEO_TEXT_UPLOAD_MB} MB limit"
        )
    if language_mode not in _LANGUAGE_SETTINGS:
        raise ValueError(f"unsupported language mode: {language_mode}")

    language, prompt = _LANGUAGE_SETTINGS[language_mode]
    suffix = os.path.splitext(filename or "")[1].lower() or ".bin"

    with tempfile.TemporaryDirectory(prefix="video-text-") as temp_dir:
        media_path = os.path.join(temp_dir, f"source{suffix}")
        wav_path = os.path.join(temp_dir, "speech.wav")
        with open(media_path, "wb") as output:
            output.write(media_bytes)

        _extract_audio(media_path, wav_path)

        # 混合语言模式使用自己的提示词；其它模式沿用 config.toml 里的 whisper.initial_prompt。
        effective_prompt = prompt or subtitle.initial_prompt
        segments, info = _transcribe(wav_path, language, effective_prompt)
        # 复用 Whisper 原始分段文本，保留句号、问号等标点，适合直接作为旁白文案。
        parts = [segment.text.strip() for segment in segments if segment.text.strip()]

    logger.info(
        f"video text extracted: mode={language_mode}, "
        f"detected_language={info.language}, segments={len(parts)}"
    )
    return " ".join(parts)
