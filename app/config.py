from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

AVAILABLE_MODELS = [
    "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash-native-audio-preview-12-2025",
]

AVAILABLE_VOICES = [
    "Aoede",
    "Puck",
    "Kore",
    "Fenrir",
    "Charon",
    "Zephyr",
]

DEFAULT_SYSTEM_PROMPT = (
    "You are a warm, concise, helpful AI assistant running on a 7-inch touchscreen smart display. "
    "Keep responses natural, clear, and conversational since they are spoken aloud. "
    "Avoid markdown formatting or long lists unless asked. When video is active, you can see through the camera."
)


@dataclass
class Settings:
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-live-preview"
    gemini_voice: str = "Aoede"
    weather_city: str = "Tel Aviv"
    weather_lat: float = 32.0853
    weather_lon: float = 34.7818
    camera_rotate: int = 0
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    def to_public_dict(self) -> dict:
        return {
            "api_key_set": bool(self.gemini_api_key.strip()),
            "gemini_model": self.gemini_model,
            "gemini_voice": self.gemini_voice,
            "weather_city": self.weather_city,
            "weather_lat": self.weather_lat,
            "weather_lon": self.weather_lon,
            "camera_rotate": self.camera_rotate,
            "system_prompt": self.system_prompt,
            "available_models": AVAILABLE_MODELS,
            "available_voices": AVAILABLE_VOICES,
        }


def load_settings() -> Settings:
    vals: dict[str, str | None] = {}
    if ENV_PATH.exists():
        vals = dotenv_values(ENV_PATH)

    def get(key: str, default: str) -> str:
        v = vals.get(key)
        if v is None or v == "":
            v = os.environ.get(key, default)
        return v if v is not None else default

    def get_float(key: str, default: float) -> float:
        try:
            return float(get(key, str(default)))
        except ValueError:
            return default

    def get_int(key: str, default: int) -> int:
        try:
            return int(get(key, str(default)))
        except ValueError:
            return default

    rotate = get_int("CAMERA_ROTATE", 0)
    if rotate not in (0, 90, 180, 270):
        rotate = 0

    return Settings(
        gemini_api_key=get("GEMINI_API_KEY", "").strip(),
        gemini_model=get("GEMINI_MODEL", "gemini-3.1-flash-live-preview").strip() or "gemini-3.1-flash-live-preview",
        gemini_voice=get("GEMINI_VOICE", "Aoede").strip() or "Aoede",
        weather_city=get("WEATHER_CITY", "Tel Aviv").strip() or "Tel Aviv",
        weather_lat=get_float("WEATHER_LAT", 32.0853),
        weather_lon=get_float("WEATHER_LON", 34.7818),
        camera_rotate=rotate,
        system_prompt=get("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT).strip() or DEFAULT_SYSTEM_PROMPT,
    )


def save_settings(s: Settings) -> None:
    lines = [
        f"GEMINI_API_KEY={s.gemini_api_key}",
        f"GEMINI_MODEL={s.gemini_model}",
        f"GEMINI_VOICE={s.gemini_voice}",
        f"WEATHER_CITY={s.weather_city}",
        f"WEATHER_LAT={s.weather_lat}",
        f"WEATHER_LON={s.weather_lon}",
        f"CAMERA_ROTATE={s.camera_rotate}",
        f"SYSTEM_PROMPT={s.system_prompt.replace(chr(10), ' ')}",
    ]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass
