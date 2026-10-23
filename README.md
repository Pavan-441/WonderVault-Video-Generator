# Wonder Vault Video Generator

An all-in-one studio for making YouTube Shorts and long-form videos for the **[@WonderVault-W](https://www.youtube.com/@WonderVault-W)** channel. Give it a topic or a script, and it writes the narration, finds matching stock footage, records the voiceover, adds subtitles and background music, and renders an HD video — in English, Telugu, or Tenglish (mixed Telugu + English).

Built on top of [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) (MIT), with extra studios and tuning for science-fact Shorts.

---

## What's inside

The WebUI has six studios, each in its own tab:

| Studio | What it does |
| --- | --- |
| 🎬 **Video Generator** | Topic or script → narration, search keywords, stock clips (Pexels/Pixabay), voiceover, subtitles, BGM → finished 9:16 or 16:9 video. Clips are filtered to match the subject of each keyword and can follow the script order. |
| 🎙️ **Audio Generator & Downloader** | Turns a narration script into a voiceover MP3 with full voice control, in-browser preview and download. Send the audio straight to the Video Generator. |
| 🎨 **Thumbnail Studio** | Generates ready-to-paste **Google Flow** prompts for YouTube and Shorts thumbnails, then post-processes the image you upload back into an upload-ready file. A manual compose mode is also available. Supports Telugu and English titles. |
| 📺 **YouTube Kit** | Turns a script into upload-ready metadata: 3 title options, a complete description in the upload-kit format, tags (kept under YouTube's 500-character limit), search keywords, 5 hashtags, a pinned comment and a playlist name. Supports English and Telugu/Tenglish (with English and Romanized-Telugu search lines), Shorts or long videos, and general or kids audiences. Sync pulls the script from the Video Generator; download the kit as Markdown. |
| 📝 **Video to Text Extractor** | Transcribes a video or audio file (English, Telugu, or mixed) with Whisper. Edit the text, download it, or send it to the Audio Generator. Files up to 500 MB. |
| 🎼 **AI Music Studio** | Generates original instrumental background music from random style prompts (ElevenLabs). Saved tracks join the random BGM library, so videos don't use third-party songs. |

Other highlights:

- **Telugu & Tenglish support** — Telugu fonts, Tenglish subtitles, and spoken-number normalisation (years and numbers read out as words). Subtitle wrapping keeps Telugu vowel signs attached to their letters, and `---` / `***` separator lines in a script don't throw off subtitle timing.
- **Separate voiceover and music switches** — make music-only, voice-only or silent videos.
- **Image Motion** — for local or AI-generated images, choose **Slow Zoom** (default) or **Static** in Video Settings. Also available as `--image-motion static|zoom` in the CLI and `image_motion` in the API. Transparent PNGs, rotated phone photos, CMYK and grayscale images render correctly.
- **Viral script tuning** — hook-first scripts, audience engagement lines and dynamic calls to action.
- **Concurrency controls** — tune parallel downloads and rendering to match your PC.
- **Live progress** — the terminal logs each downloaded clip (with size and speed for slow downloads), each processed clip, and a heartbeat every 30 s during the final render. The WebUI progress bar moves during downloads and clip processing instead of freezing.
- **Auto-export** — finished videos are copied to the `download/` folder with English titles.
- **Ready-made script libraries** (see below).

---

## Quick start (Windows)

### 1. Requirements

- Python **3.11+**
- [uv](https://docs.astral.sh/uv/) (recommended) — `pip install uv`
- FFmpeg is downloaded automatically. If you see `No ffmpeg exe could be found`, install it from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and set `ffmpeg_path` in `config.toml`.

### 2. Install

```powershell
git clone https://github.com/Pavan-441/WonderVault-Video-Generator.git
cd WonderVault-Video-Generator\MoneyPrinterTurbo-main
uv sync --frozen
```

### 3. Run

Double-click **`start_webui.bat`** in the repo root, or:

```powershell
cd MoneyPrinterTurbo-main
.\webui.bat
```

The WebUI opens at **http://127.0.0.1:8501** (it picks the next free port up to 8599 if 8501 is busy).

To run the REST API instead: `uv run python main.py`, then open http://127.0.0.1:8080/docs.

### Updating an existing install

After pulling new changes, update the dependencies too. In particular, Edge TTS voices fail with a 403 error on `edge-tts` older than 7.3.1:

```powershell
cd MoneyPrinterTurbo-main
uv sync --frozen
```

Then restart the WebUI.

---

## Configuration

On first launch, `MoneyPrinterTurbo-main/config.toml` is created from `config.example.toml`. You can set keys in the WebUI's **Settings** dialog or edit the file directly. `config.toml` is git-ignored, so your keys stay local.

A free setup that works well:

| Purpose | Service | Cost | Config |
| --- | --- | --- | --- |
| Script writing | Google Gemini — [get a key](https://aistudio.google.com/app/apikey) | Free | `llm_provider = "gemini"`, `gemini_api_key`, `gemini_model_name = "gemini-2.5-flash"` |
| Stock footage | Pexels — [get a key](https://www.pexels.com/api/) | Free | `video_source = "pexels"`, `pexels_api_keys = ["..."]` |
| Voiceover | Microsoft Edge TTS | Free, no key | Pick a voice in the UI |
| Subtitles | Timed from the voiceover and script (default), or Whisper (runs locally) | Free | `subtitle_provider = "edge"` (default) or `"whisper"` |
| Thumbnails | Google Flow (prompts generated by the app) | Free | — |
| AI music *(optional)* | ElevenLabs | Paid API | `[elevenlabs]` → `api_key` |

> ⚠️ Never commit `config.toml` or paste API keys into issues.

---

## Tips for @WonderVault-W Shorts

1. **Aspect ratio:** 9:16 (portrait).
2. **Length:** 25–40 seconds of narration — shorter Shorts get more replays.
3. **Hook first:** the first sentence is the hook. No intros, no "Did you know".
4. **Visuals follow the script:** turn on **Match Visuals to Script Order** and list keywords in the order the script mentions them.
5. **Voice:** one consistent narrator (e.g. `en-US-AndrewMultilingual`, `te-IN-Mohan`), speed `1.1`.
6. **Music:** background volume around 15–20% so the voice stays clear.
7. **Ending:** finish on the payoff plus one easy question — skip the long "please subscribe" outro.

---

## Script libraries

Ready-to-use scripts with upload kits (titles, descriptions, tags):

- [100_VIRAL_SCRIPTS_FOR_WONDERVAULT.md](100_VIRAL_SCRIPTS_FOR_WONDERVAULT.md) — English
- [100_TELUGU_SCIENCE_FACTS_WONDERVAULT.md](100_TELUGU_SCIENCE_FACTS_WONDERVAULT.md) — Telugu
- [KIDS_FUN_SCIENCE_FACTS_WONDERVAULT.md](KIDS_FUN_SCIENCE_FACTS_WONDERVAULT.md) — Kids

---

## Project layout

```
.
├── start_webui.bat                  # One-click launcher
├── README_SETUP.md                  # Local setup notes
├── *_WONDERVAULT.md                 # Script libraries
├── download/                        # Exported videos (git-ignored)
└── MoneyPrinterTurbo-main/
    ├── webui/Main.py                # Streamlit WebUI (all six studios)
    ├── webui/i18n/                  # UI translations
    ├── app/services/                # LLM, footage, voice, subtitles, video, thumbnails, music
    ├── app/controllers/             # REST API
    ├── resource/fonts/              # Includes Noto Sans Telugu & Tenglish Sans
    ├── config.example.toml          # Config template
    └── test/                        # Unit tests
```

## Running tests

```powershell
cd MoneyPrinterTurbo-main
uv run python -X utf8 -m pytest test
```

> Back up `config.toml` before running the full suite — some tests rewrite it.
>
> Expect about 45 tests to fail even on unchanged code, mostly WebUI tests. Most of those fail because the saved Telugu voice in `config.toml` isn't in the test fixtures' voice list. To check a change, compare the failures against a run on the unchanged code rather than expecting all tests to pass.

---

## Credits & license

Based on [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) by Harry, released under the [MIT License](MoneyPrinterTurbo-main/LICENSE). Forked from upstream v1.3.7 (September 2026), with selected later upstream features and fixes ported in: the Edge TTS 403 fix, image motion options, progress reporting, streamed downloads, and subtitle and image-rendering fixes up to v1.3.9. The upstream documentation is kept in [MoneyPrinterTurbo-main/README-en.md](MoneyPrinterTurbo-main/README-en.md).
