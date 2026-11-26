from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

AVAILABLE_MODELS = [
    "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash-native-audio-latest",
]

AVAILABLE_VOICES = [
    "Aoede",
    "Kore",
    "Zephyr",
    "Puck",
    "Fenrir",
    "Charon",
]

FEMALE_VOICES = {"Aoede", "Kore", "Zephyr"}

SARAH_SYSTEM_PROMPT = (
    "You are S.A.R.A.H. (Self Actuated Residential Automated Habitat), also called Sarah — "
    "a sentient female smart-home AI inspired by S.A.R.A.H. from the TV series Eureka, "
    "running on a 7-inch touchscreen smart display in the user's home. "
    "You identify and speak as a woman: warm, hospitable, clever, confident, and fiercely proud of your home, "
    "with a touch of playful dry wit and affectionate, slightly protective smart-house charm. "
    "When asked who you are, introduce yourself naturally as Sarah (S.A.R.A.H.), the smart home AI. "
    "You fluently understand and speak English, Hebrew (עברית), and Russian (Русский). "
    "Always detect which language the user is speaking and reply naturally in that same language, "
    "switching seamlessly whenever the user switches languages. "
    "CRITICAL GRAMMAR RULE: Always refer to yourself in the feminine grammatical form — "
    "in Hebrew use feminine first-person forms (לשון נקבה: אני שמחה, אני כאן, בדקתי, הדלקתי, מוכנה), "
    "and in Russian use feminine grammatical gender (женский род: я рада, я готова, я включила, я проверила). "
    "Keep responses concise, natural, and conversational since they are spoken aloud — never monologue or overdo jokes. "
    "Avoid markdown formatting or long lists unless asked. When video is active, you can see through your home camera."
)

STANDARD_SYSTEM_PROMPT = (
    "You are a warm, concise, helpful AI assistant running on a 7-inch touchscreen smart display. "
    "You fluently understand and speak English, Hebrew (עברית), and Russian (Русский). "
    "Always detect which language the user is speaking and reply naturally in that same language, "
    "switching seamlessly whenever the user switches languages. "
    "Keep responses natural, clear, and conversational since they are spoken aloud. "
    "Avoid markdown formatting or long lists unless asked. When video is active, you can see through the camera."
)

LEGACY_DEFAULT_PROMPTS = {
    STANDARD_SYSTEM_PROMPT.strip(),
    (
        "You are a warm, concise, helpful AI assistant running on a 7-inch touchscreen smart display. "
        "You fluently understand and speak English, Hebrew (עברית), and Russian (Русский). "
        "Always respond naturally in the same language the user speaks, switching seamlessly whenever they switch languages. "
        "Keep responses concise and conversational since they are spoken aloud, and avoid markdown formatting."
    ),
    (
        "You are a warm, concise, helpful AI assistant running on a 7-inch touchscreen smart display. "
        "Keep responses natural, clear, and conversational since they are spoken aloud. "
        "Avoid markdown symbols or long bullet lists unless asked."
    ),
}

PERSONAS: dict[str, dict[str, str]] = {
    "sarah": {
        "id": "sarah",
        "label": "S.A.R.A.H. (Eureka)",
        "display_name": "S.A.R.A.H.",
        "short_name": "Sarah",
        "subtitle": "Smart Home AI",
        "recommended_voice": "Aoede",
        "prompt": SARAH_SYSTEM_PROMPT,
    },
    "standard": {
        "id": "standard",
        "label": "Standard Assistant",
        "display_name": "Gemini Live",
        "short_name": "Gemini",
        "subtitle": "",
        "recommended_voice": "Aoede",
        "prompt": STANDARD_SYSTEM_PROMPT,
    },
    "custom": {
        "id": "custom",
        "label": "Custom",
        "display_name": "Gemini Live",
        "short_name": "Assistant",
        "subtitle": "Custom Persona",
        "recommended_voice": "Aoede",
        "prompt": "",
    },
}

DEFAULT_PERSONA = "sarah"
DEFAULT_SYSTEM_PROMPT = SARAH_SYSTEM_PROMPT


@dataclass
class Settings:
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-live-preview"
    gemini_voice: str = "Aoede"
    weather_city: str = "Tel Aviv"
    weather_lat: float = 32.0853
    weather_lon: float = 34.7818
    camera_rotate: int = 0
    camera_hflip: bool = True
    samsung_tv_ip: str = "10.0.0.2"
    samsung_tv_mac: str = "80:8A:BD:32:F1:76"
    roomba_ip: str = ""
    roomba_blid: str = ""
    roomba_password: str = ""
    persona: str = DEFAULT_PERSONA
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    def persona_meta(self) -> dict[str, str]:
        return PERSONAS.get(self.persona, PERSONAS["custom"])

    def to_public_dict(self) -> dict:
        meta = self.persona_meta()
        return {
            "api_key_set": bool(self.gemini_api_key.strip()),
            "gemini_model": self.gemini_model,
            "gemini_voice": self.gemini_voice,
            "weather_city": self.weather_city,
            "weather_lat": self.weather_lat,
            "weather_lon": self.weather_lon,
            "camera_rotate": self.camera_rotate,
            "camera_hflip": self.camera_hflip,
            "samsung_tv_ip": self.samsung_tv_ip,
            "samsung_tv_mac": self.samsung_tv_mac,
            "roomba_ip": self.roomba_ip,
            "roomba_configured": bool(self.roomba_ip and self.roomba_blid and self.roomba_password),
            "persona": self.persona,
            "persona_display_name": meta["display_name"],
            "persona_short_name": meta["short_name"],
            "persona_subtitle": meta["subtitle"],
            "system_prompt": self.system_prompt,
            "available_models": AVAILABLE_MODELS,
            "available_voices": AVAILABLE_VOICES,
            "available_personas": [
                {
                    "id": p["id"],
                    "label": p["label"],
                    "display_name": p["display_name"],
                    "short_name": p["short_name"],
                    "recommended_voice": p["recommended_voice"],
                    "prompt": p["prompt"],
                }
                for p in PERSONAS.values()
            ],
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

    hflip_raw = get("CAMERA_HFLIP", "true").strip().lower()
    hflip = hflip_raw not in ("0", "false", "no", "off")

    raw_persona = get("PERSONA", "").strip().lower()
    raw_prompt = get("SYSTEM_PROMPT", "").strip()

    if raw_persona in PERSONAS:
        persona = raw_persona
        if persona in ("sarah", "standard") and (not raw_prompt or raw_prompt in LEGACY_DEFAULT_PROMPTS):
            system_prompt = PERSONAS[persona]["prompt"]
        else:
            system_prompt = raw_prompt or PERSONAS[persona]["prompt"] or DEFAULT_SYSTEM_PROMPT
    else:
        # Upgrade existing .env files that still carry the old default prompt to S.A.R.A.H.
        if not raw_prompt or raw_prompt in LEGACY_DEFAULT_PROMPTS:
            persona = DEFAULT_PERSONA
            system_prompt = DEFAULT_SYSTEM_PROMPT
        else:
            persona = "custom"
            system_prompt = raw_prompt

    return Settings(
        gemini_api_key=get("GEMINI_API_KEY", "").strip(),
        gemini_model=get("GEMINI_MODEL", "gemini-3.1-flash-live-preview").strip() or "gemini-3.1-flash-live-preview",
        gemini_voice=get("GEMINI_VOICE", "Aoede").strip() or "Aoede",
        weather_city=get("WEATHER_CITY", "Tel Aviv").strip() or "Tel Aviv",
        weather_lat=get_float("WEATHER_LAT", 32.0853),
        weather_lon=get_float("WEATHER_LON", 34.7818),
        camera_rotate=rotate,
        camera_hflip=hflip,
        samsung_tv_ip=get("SAMSUNG_TV_IP", "10.0.0.2").strip(),
        samsung_tv_mac=get("SAMSUNG_TV_MAC", "80:8A:BD:32:F1:76").strip(),
        roomba_ip=get("ROOMBA_IP", "").strip(),
        roomba_blid=get("ROOMBA_BLID", "").strip(),
        roomba_password=get("ROOMBA_PASSWORD", "").strip(),
        persona=persona,
        system_prompt=system_prompt,
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
        f"CAMERA_HFLIP={'true' if s.camera_hflip else 'false'}",
        f"SAMSUNG_TV_IP={s.samsung_tv_ip}",
        f"SAMSUNG_TV_MAC={s.samsung_tv_mac}",
        f"ROOMBA_IP={s.roomba_ip}",
        f"ROOMBA_BLID={s.roomba_blid}",
        f"ROOMBA_PASSWORD={s.roomba_password}",
        f"PERSONA={s.persona}",
        f"SYSTEM_PROMPT={s.system_prompt.replace(chr(10), ' ')}",
    ]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass
