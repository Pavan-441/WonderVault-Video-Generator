# WonderVault-W YouTube Automation Setup

This folder contains the complete local setup for **Wonder Vault Video Generator** dedicated to your YouTube channel **@WonderVault-W**.

---

## 📁 Installation Directory
- **Project Path**: `D:\MoneyPrinterTurbo\MoneyPrinterTurbo-main`
- **Virtual Environment**: `D:\MoneyPrinterTurbo\MoneyPrinterTurbo-main\.venv`
- **Configuration File**: `D:\MoneyPrinterTurbo\MoneyPrinterTurbo-main\config.toml`
- **Exported Videos**: `D:\MoneyPrinterTurbo\download`

---

## 🚀 How to Run

Double-click the launcher script:
`D:\MoneyPrinterTurbo\start_webui.bat`

Or run via terminal:
```powershell
cd D:\MoneyPrinterTurbo\MoneyPrinterTurbo-main
.\webui.bat
```
The WebUI will open automatically in your browser at `http://127.0.0.1:8501`.

Progress is logged in the terminal window while a video is generated: each downloaded clip, each processed clip, and a "still running" line every 30 seconds during the final render.

---

## 🔄 Updating Dependencies

After pulling new code, update the packages in the virtual environment, then restart the WebUI:
```powershell
cd D:\MoneyPrinterTurbo\MoneyPrinterTurbo-main
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```
Edge TTS voices fail with a 403 error on `edge-tts` older than 7.3.1, so run this whenever Edge voices stop working.

---

## 🔑 Configured API Keys & Settings (`config.toml`)

Open `D:\MoneyPrinterTurbo\MoneyPrinterTurbo-main\config.toml` to customize or verify your keys:

### 1. Script Generation (Google Gemini - 100% Free)
```toml
llm_provider = "gemini"
gemini_api_key = "YOUR_GEMINI_API_KEY"
gemini_model_name = "gemini-2.5-flash"  # or gemini-1.5-flash
```
*Get your free key with no credit card at: https://aistudio.google.com/app/apikey*

### 2. Video Clips (Pexels HD Stock Footage - Free)
```toml
video_source = "pexels"
pexels_api_keys = ["YOUR_PEXELS_API_KEY"]
```
*Get your free key at: https://www.pexels.com/api/*

To use your own Google Flow images instead, set **Video Source** to **Local file** in the WebUI and upload them. The **Image Motion** setting chooses **Slow Zoom** (default) or **Static**; for completely still images, also set **Video Transition Mode** to **None**.

### 3. Voiceover
- **Edge TTS** (free, no key): human-like voices such as `en-US-ChristopherNeural`, `en-US-JennyNeural` or `te-IN-MohanNeural`.
- **Gemini TTS**: uses your Gemini API key. Pick it under **Voiceover Service** in the WebUI.

### 4. Subtitles
Subtitles are generated locally on your PC. By default they are timed from the voiceover and the script:
```toml
subtitle_provider = "edge"     # default: timed from the voiceover and script
# subtitle_provider = "whisper"  # optional: transcribe the audio with Whisper instead
```
Separator lines such as `---` or `***` in a script are not spoken and don't affect subtitle timing.

---

## 💡 Best Practices for @WonderVault-W Shorts
1. **Aspect Ratio**: Select **9:16 (Portrait)** for YouTube Shorts.
2. **Video Length**: Keep narration between 25–40 seconds — shorter Shorts get a higher percentage viewed and more replays.
3. **Hook first**: The first sentence is the hook. No intros, no channel name, no "Did you know".
4. **Visuals follow the script**: Turn on **Match Visuals to Script Order** and list keywords in the order the script mentions them.
5. **Voice**: One consistent narrator per channel (e.g. `en-US-AndrewMultilingual-Male`, `te-IN-Mohan (Dynamic & High Energy)`), Voice Speed `1.1`.
6. **Music**: Background music volume around 15–20% so the voice is prominent.
7. **Ending**: End on the payoff plus one easy question — no long "please subscribe" outro.
8. **Upload metadata**: After generating a video, open the **📺 YouTube Kit** tab, click **Sync**, then **Generate YouTube Kit** to get the title options, description, tags, keywords and hashtags.
9. **Ready-made scripts**: See the full reach playbook and upload kits in `100_VIRAL_SCRIPTS_FOR_WONDERVAULT.md` (English), `100_TELUGU_SCIENCE_FACTS_WONDERVAULT.md` (Telugu) and `KIDS_FUN_SCIENCE_FACTS_WONDERVAULT.md` (Kids).
