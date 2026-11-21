# Gemini Smart Display

Touch-optimized web UI and real-time voice/video Gemini Live AI assistant built for a Raspberry Pi 4 (7" `1024×600` DSI touchscreen, CSI camera, USB microphone, and 3.5 mm speaker).

## Features
- **Always-on Smart Display Dashboard (`1024×600`)**: Crisp 24-hour clock, weekday, date, current local weather, and 4-day forecast (powered by [Open-Meteo](https://open-meteo.com/), no API key needed).
- **Real-Time Gemini Live Voice & Video Chat**: Bidirectional audio streaming via the `google-genai` Live API (`gemini-3.1-flash-live-preview` / `gemini-2.5-flash-native-audio-preview-12-2025`) with jitter-buffered `AudioWorklet` playback, anti-aliased 16 kHz mic capture, WebRTC echo cancellation, instant barge-in interruption, and live conversation transcripts.
- **On-Demand CSI Camera (`picamera2`)**: Live MJPEG preview and 1 fps video frames to Gemini only while Video Chat (or Settings camera test) is active; automatically turns off when idle.
- **Touch Controls**: Built-in hardware volume control (`wpctl` / PipeWire) and a Settings drawer for city search, Gemini voice/model selection, camera rotation (`0°`/`90°`/`180°`/`270°`), and API key management.

## Quick Setup (Raspberry Pi OS / Debian Trixie)

```bash
git clone <your-repo-url> ~/gemini-display
cd ~/gemini-display
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Add your Google AI Studio key to `.env` (`GEMINI_API_KEY=...`), or configure it via the web UI at `http://<pi-ip>:8000` → **Settings**.

### Systemd User Service & Kiosk Autostart

```bash
mkdir -p ~/.config/systemd/user
cp gemini-display.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gemini-display.service
chmod +x start-kiosk.sh
```

Add `/home/<user>/gemini-display/start-kiosk.sh &` to `~/.config/labwc/autostart` (for Wayland/`labwc`) to launch Chromium in fullscreen kiosk mode on boot.
