# WonderVault-W YouTube Automation Setup

This folder contains the complete local setup for **MoneyPrinterTurbo** dedicated to your YouTube channel **@WonderVault-W**.

---

## 📁 Installation Directory
- **Project Path**: `D:\youtube-automation\MoneyPrinterTurbo`
- **Virtual Environment**: `D:\youtube-automation\MoneyPrinterTurbo\.venv`
- **Configuration File**: `D:\youtube-automation\MoneyPrinterTurbo\config.toml`

---

## 🚀 How to Run

Double-click the launcher script:
`D:\youtube-automation\start_webui.bat`

Or run via terminal:
```powershell
cd D:\youtube-automation\MoneyPrinterTurbo
.\webui.bat
```
The WebUI will open automatically in your browser at `http://127.0.0.1:8501`.

---

## 🔑 Configured API Keys & Settings (`config.toml`)

Open `D:\youtube-automation\MoneyPrinterTurbo\config.toml` to customize or verify your keys:

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

### 3. Voiceover & Subtitles (Edge TTS - Free, No Key Required)
- **TTS**: Microsoft Edge TTS runs automatically for free with human-like voices (e.g., `en-US-ChristopherNeural` or `en-US-JennyNeural`).
- **Subtitles**: Subtitle synchronization runs locally on your PC.

---

## 💡 Best Practices for @WonderVault-W Shorts
1. **Aspect Ratio**: Select **9:16 (Portrait)** for YouTube Shorts.
2. **Video Length**: Keep narration between 45–55 seconds for high retention.
3. **Voice**: Select an expressive, engaging English narrator voice.
4. **Music**: Choose an atmospheric or curious background music track with volume set around 15–20% so the voice is prominent.
