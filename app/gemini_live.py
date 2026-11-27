from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from datetime import datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

from .camera import CameraManager
from .config import AVAILABLE_SPEAKING_PACES, DEFAULT_SPEAKING_PACE, Settings
from .news import GoogleNewsService
from .roomba import RoombaController
from .samsung_tv import SamsungTVController
from .weather import WeatherService

logger = logging.getLogger(__name__)

ALLOWED_LANGUAGES = ["en-US", "he-IL", "ru-RU"]


def _tv_function_declarations() -> list[types.FunctionDeclaration]:
    return [
        types.FunctionDeclaration(
            name="tv_status",
            description="Check whether the user's Samsung Smart TV is currently on or in standby.",
            parameters=types.Schema(type=types.Type.OBJECT, properties={}),
        ),
        types.FunctionDeclaration(
            name="tv_power",
            description="Turn the user's Samsung Smart TV on or off (standby).",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "action": types.Schema(
                        type=types.Type.STRING,
                        enum=["on", "off"],
                        description="Whether to turn the TV 'on' or 'off'.",
                    )
                },
                required=["action"],
            ),
        ),
        types.FunctionDeclaration(
            name="tv_volume",
            description="Adjust the Samsung Smart TV volume up/down or toggle mute.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "action": types.Schema(
                        type=types.Type.STRING,
                        enum=["up", "down", "mute"],
                        description="Volume action: 'up', 'down', or 'mute'.",
                    ),
                    "steps": types.Schema(
                        type=types.Type.INTEGER,
                        description="Number of volume steps (1 to 15, default 3). Ignored for mute.",
                    ),
                },
                required=["action"],
            ),
        ),
        types.FunctionDeclaration(
            name="tv_control",
            description=(
                "Send playback, navigation, channel, or input source commands to the Samsung Smart TV "
                "(play, pause, stop, rewind, fast_forward, home, back, enter, up, down, left, right, "
                "channel_up, channel_down, source, hdmi1, hdmi2, hdmi3, hdmi4, ambient, menu, guide)."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "action": types.Schema(
                        type=types.Type.STRING,
                        description="Remote control action name.",
                    )
                },
                required=["action"],
            ),
        ),
        types.FunctionDeclaration(
            name="tv_open_app",
            description=(
                "Launch an app on the Samsung Smart TV by name (e.g., YouTube, Netflix, Spotify, "
                "Prime Video, Disney+, Apple TV, Plex, Browser). Wakes the TV automatically if off."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "app_name": types.Schema(
                        type=types.Type.STRING,
                        description="Name of the app to open on the TV.",
                    )
                },
                required=["app_name"],
            ),
        ),
        types.FunctionDeclaration(
            name="tv_open_url",
            description="Open a web page URL in the Samsung Smart TV's built-in Internet browser.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "url": types.Schema(
                        type=types.Type.STRING,
                        description="Full URL to open on the TV browser.",
                    )
                },
                required=["url"],
            ),
        ),
    ]


def _roomba_function_declarations() -> list[types.FunctionDeclaration]:
    return [
        types.FunctionDeclaration(
            name="roomba_status",
            description=(
                "Check the iRobot Roomba robot vacuum's live status: battery percentage, current state "
                "(charging on dock, cleaning, paused, returning to dock, stuck), whether the dustbin is "
                "full or missing, any error codes, and suction/passes settings."
            ),
            parameters=types.Schema(type=types.Type.OBJECT, properties={}),
        ),
        types.FunctionDeclaration(
            name="roomba_command",
            description=(
                "Send a control command to the iRobot Roomba robot vacuum: 'start' to begin cleaning, "
                "'pause' to pause, 'resume' to continue a paused cycle, 'stop' to stop, 'dock' to return "
                "to the charging Home Base, or 'find' to play a locating chime/beep."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "action": types.Schema(
                        type=types.Type.STRING,
                        enum=["start", "pause", "resume", "stop", "dock", "find"],
                        description="Command to send to the Roomba.",
                    )
                },
                required=["action"],
            ),
        ),
        types.FunctionDeclaration(
            name="roomba_settings",
            description=(
                "Adjust the iRobot Roomba's cleaning settings: suction power ('auto' for Carpet Boost, "
                "'high' for max performance, 'eco' for quiet/low), cleaning passes ('auto', 'one', 'two'), "
                "or pause when dustbin is full ('bin_pause')."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "suction": types.Schema(
                        type=types.Type.STRING,
                        enum=["auto", "high", "eco"],
                        description="Suction mode: 'auto', 'high', or 'eco'.",
                    ),
                    "passes": types.Schema(
                        type=types.Type.STRING,
                        enum=["auto", "one", "two"],
                        description="Cleaning passes: 'auto', 'one', or 'two'.",
                    ),
                    "bin_pause": types.Schema(
                        type=types.Type.BOOLEAN,
                        description="Whether to pause cleaning when the dustbin becomes full.",
                    ),
                },
            ),
        ),
    ]


def _news_function_declarations() -> list[types.FunctionDeclaration]:
    return [
        types.FunctionDeclaration(
            name="news_briefing",
            description=(
                "Fetch a live Google News briefing covering the user's followed topics "
                "(Israel, World, Technology/AI, Science, Business/Finance, Astronomy, etc.). "
                "Use when the user asks for the news, a daily briefing, or what's happening in the world."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "language": types.Schema(
                        type=types.Type.STRING,
                        enum=["en", "he", "ru"],
                        description="Preferred news language edition: 'en' (English), 'he' (Hebrew/Israel), or 'ru' (Russian). Default 'en'.",
                    ),
                    "per_topic": types.Schema(
                        type=types.Type.INTEGER,
                        description="Number of headlines per topic (1 to 4, default 2).",
                    ),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="news_headlines",
            description=(
                "Fetch live Google News headlines for a specific category and edition. "
                "Use when the user asks about a specific section like Israel news, World news, "
                "Technology, Science, Business/Finance, Health, Sports, or Entertainment."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "category": types.Schema(
                        type=types.Type.STRING,
                        enum=[
                            "top",
                            "world",
                            "israel",
                            "technology",
                            "science",
                            "business",
                            "health",
                            "sports",
                            "entertainment",
                        ],
                        description="News section/category.",
                    ),
                    "edition": types.Schema(
                        type=types.Type.STRING,
                        enum=["il_en", "il_he", "world", "ru"],
                        description="Google News edition: 'il_en' (Israel English), 'il_he' (Israel Hebrew), 'world' (US/Global English), or 'ru' (Russian).",
                    ),
                    "limit": types.Schema(
                        type=types.Type.INTEGER,
                        description="Number of stories to return (1 to 10, default 6).",
                    ),
                },
                required=["category"],
            ),
        ),
        types.FunctionDeclaration(
            name="news_search",
            description=(
                "Search live Google News for any specific topic, event, person, company, or follow-up story "
                "(e.g., 'astronomy', 'James Webb telescope', 'AI models', 'NVIDIA', 'בחירות'). "
                "Use this both for specific topic requests and when the user wants more details about a story."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description="Search keywords or topic phrase.",
                    ),
                    "language": types.Schema(
                        type=types.Type.STRING,
                        enum=["en", "he", "ru", "il_en"],
                        description="News language/edition ('en', 'he', 'ru', or 'il_en'). Default 'en'.",
                    ),
                    "days": types.Schema(
                        type=types.Type.INTEGER,
                        description="Recency window in days (1 to 30, default 7).",
                    ),
                    "limit": types.Schema(
                        type=types.Type.INTEGER,
                        description="Number of stories to return (1 to 10, default 6).",
                    ),
                },
                required=["query"],
            ),
        ),
    ]


def build_smart_home_tools(
    tv: SamsungTVController | None,
    roomba: RoombaController | None,
    news: GoogleNewsService | None = None,
) -> list[types.Tool]:
    declarations: list[types.FunctionDeclaration] = []
    if tv and tv.configured:
        declarations.extend(_tv_function_declarations())
    if roomba and roomba.configured:
        declarations.extend(_roomba_function_declarations())
    if news is not None:
        declarations.extend(_news_function_declarations())
    if not declarations:
        return []
    return [types.Tool(function_declarations=declarations)]


def _execute_tv_tool(tv: SamsungTVController, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if name == "tv_status":
        res = tv.get_status()
        state = res.get("power_state", "unknown")
        return res, f"TV status: {state}"
    if name == "tv_power":
        act = str(args.get("action", "")).lower()
        res = tv.power(act)
        return res, f"TV power: {act}"
    if name == "tv_volume":
        act = str(args.get("action", "")).lower()
        steps = int(args.get("steps", 3) or 3)
        res = tv.volume(act, steps=steps)
        label = "TV mute" if act == "mute" else f"TV volume {act} (×{steps})"
        return res, label
    if name == "tv_control":
        act = str(args.get("action", ""))
        res = tv.control(act)
        return res, f"TV: {act}"
    if name == "tv_open_app":
        app_name = str(args.get("app_name", ""))
        res = tv.open_app(app_name)
        return res, f"TV app: {res.get('app', app_name)}"
    if name == "tv_open_url":
        url = str(args.get("url", ""))
        res = tv.open_url(url)
        return res, f"TV browser: {url}"
    return {"ok": False, "error": f"Unknown tool: {name}"}, f"Unknown tool: {name}"

def _execute_roomba_tool(
    roomba: RoombaController,
    name: str,
    args: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    if name == "roomba_status":
        res = roomba.get_status()
        state = res.get("state", "unknown")
        bat = res.get("battery_pct")
        suffix = f" ({bat}%)" if bat is not None else ""
        return res, f"Roomba: {state}{suffix}"
    if name == "roomba_command":
        act = str(args.get("action", "")).lower()
        res = roomba.command(act)
        return res, f"Roomba: {act}"
    if name == "roomba_settings":
        suction = args.get("suction")
        passes = args.get("passes")
        bin_pause = args.get("bin_pause")
        res = roomba.set_settings(
            suction=str(suction) if suction is not None else None,
            passes=str(passes) if passes is not None else None,
            bin_pause=bool(bin_pause) if bin_pause is not None else None,
        )
        return res, "Roomba settings updated"
    return {"ok": False, "error": f"Unknown tool: {name}"}, f"Unknown tool: {name}"


def _execute_news_tool(
    news: GoogleNewsService,
    settings: Settings,
    name: str,
    args: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    if name == "news_briefing":
        lang = str(args.get("language", "en") or "en").lower()
        per_topic = int(args.get("per_topic", 2) or 2)
        res = news.get_briefing(settings.news_topics_list(), language=lang, per_topic=per_topic)
        return res, f"News briefing ({lang.upper()})"
    if name == "news_headlines":
        cat = str(args.get("category", "top") or "top")
        ed = str(args.get("edition", "world") or "world")
        limit = int(args.get("limit", 6) or 6)
        res = news.get_headlines(category=cat, edition=ed, limit=limit)
        return res, f"News: {cat} ({ed})"
    if name == "news_search":
        q = str(args.get("query", "") or "")
        lang = str(args.get("language", "en") or "en")
        days = int(args.get("days", 7) or 7)
        limit = int(args.get("limit", 6) or 6)
        res = news.search_news(query=q, language=lang, days=days, limit=limit)
        return res, f"News search: {q}"
    return {"ok": False, "error": f"Unknown tool: {name}"}, f"Unknown tool: {name}"


def _execute_smart_home_tool(
    tv: SamsungTVController | None,
    roomba: RoombaController | None,
    news: GoogleNewsService | None,
    settings: Settings,
    name: str,
    args: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    if name.startswith("tv_") and tv:
        res, label = _execute_tv_tool(tv, name, args)
        return res, label, "📺"
    if name.startswith("roomba_") and roomba:
        res, label = _execute_roomba_tool(roomba, name, args)
        return res, label, "🤖"
    if name.startswith("news_") and news:
        res, label = _execute_news_tool(news, settings, name, args)
        return res, label, "📰"
    return {"ok": False, "error": f"Unknown or unavailable tool: {name}"}, f"Unknown tool: {name}", "⚙️"


# Allowed characters: ASCII (English), Hebrew (U+0590-U+05FF), Russian Cyrillic (А-Я, а-я, Ё, ё),
# general punctuation/bidi marks (U+2000-U+206F), currency (₪/€/₽, U+20A0-U+20CF), and safe symbols (°£§©«®±²³·»¼-¾×÷№™).
_DISALLOWED_SCRIPT_RE = re.compile(
    r"[^\x00-\x7F\u00A0\u00A3\u00A7\u00A9\u00AB\u00AE\u00B0-\u00B3\u00B7\u00BB-\u00BE\u00D7\u00F7"
    r"\u0401\u0410-\u044F\u0451\u0590-\u05FF\u2000-\u206F\u20A0-\u20CF\u2116\u2122\s]"
)
_FOREIGN_PUNCT = {"¡", "¿"}


def _has_disallowed_letter(text: str) -> bool:
    for ch in _DISALLOWED_SCRIPT_RE.findall(text):
        if ch in _FOREIGN_PUNCT or unicodedata.category(ch).startswith(("L", "M")):
            return True
    return False


def _filter_transcript(text: str, *, strict_chunk: bool = False) -> str | None:
    """Keep only English, Hebrew, and Russian transcript content."""
    if not text:
        return None
    if not _DISALLOWED_SCRIPT_RE.search(text):
        return text

    if strict_chunk and _has_disallowed_letter(text):
        return None

    parts = re.split(r"(\s+)", text)
    kept: list[str] = []
    for part in parts:
        if not part:
            continue
        if part.isspace():
            kept.append(part)
            continue
        if _has_disallowed_letter(part):
            continue
        cleaned = _DISALLOWED_SCRIPT_RE.sub("", part)
        if cleaned:
            kept.append(cleaned)

    result = "".join(kept)
    if not result.strip():
        return None
    # Collapse runs of spaces created by dropped tokens while preserving original edge spaces
    lead = " " if text[0].isspace() else ""
    trail = " " if text[-1].isspace() else ""
    core = re.sub(r"[ \t]{2,}", " ", result.strip())
    return f"{lead}{core}{trail}" if core else None


def _is_allowed_transcript(text: str) -> bool:
    return _filter_transcript(text, strict_chunk=True) is not None


async def build_system_instruction(
    settings: Settings,
    weather_service: WeatherService,
    tv: SamsungTVController | None = None,
    roomba: RoombaController | None = None,
    news: GoogleNewsService | None = None,
) -> str:
    now = datetime.now()
    pace_meta = AVAILABLE_SPEAKING_PACES.get(
        settings.speaking_pace,
        AVAILABLE_SPEAKING_PACES[DEFAULT_SPEAKING_PACE],
    )
    context_lines = [
        settings.system_prompt,
        pace_meta["instruction"],
        "STRICT LANGUAGE POLICY: You and the user ONLY communicate in English, Hebrew (עברית), or Russian (Русский). "
        "NEVER speak, reply, quote, or write transcriptions in any other language (never Arabic, Ukrainian, Spanish, "
        "French, German, Chinese, etc.). "
        "Never interpret unclear audio, pauses, or background noise as any language other than English, Hebrew, or Russian. "
        "If a tool result, proper name, or news headline contains another language, translate it naturally into the active "
        "language (English, Hebrew, or Russian). "
        "Always reply naturally in whichever of those three languages (English, Hebrew, or Russian) the user spoke, "
        "switching seamlessly if the user switches between them.",
        f"Current local date and time: {now.strftime('%A, %d %B %Y, %H:%M')}.",
        f"Location: {settings.weather_city}.",
    ]
    if tv and tv.configured:
        context_lines.append(
            "Smart Home TV Control: You are connected to the user's Samsung Smart TV (Samsung QN90BA 55). "
            "Whenever the user asks (in English, Hebrew, or Russian) to turn the TV on/off, change volume, mute, "
            "pause/play, navigate, change channels/HDMI, open YouTube/Netflix/Spotify/apps, or open a URL on the TV, "
            "immediately invoke the appropriate tv_* tool and give a short natural confirmation in the user's language."
        )
    if roomba and roomba.configured:
        context_lines.append(
            "Smart Home Roomba Vacuum Control: You are connected to the user's iRobot Roomba 976 robot vacuum. "
            "Whenever the user asks (in English, Hebrew — e.g. רומבה / שואב אבק, or Russian — e.g. румба / пылесос) "
            "to check the Roomba's status, battery, or dustbin, start cleaning, pause, resume, stop, send it back to "
            "the charging dock/base, locate/beep it, or change suction/passes settings, immediately invoke the "
            "appropriate roomba_* tool and give a short natural confirmation in the user's language."
        )
    if news is not None:
        context_lines.append(
            f"Google News & World Events: You have live access to Google News via news_briefing, news_headlines, "
            f"and news_search. The user's followed news topics are: {settings.news_topics}. "
            "Whenever the user asks what is happening in the world, in Israel, or in any topic (in English, Hebrew, "
            "or Russian), always call the appropriate news_* tool first instead of guessing. "
            "For Israel news in Hebrew use edition 'il_he'; for Israel news in English use 'il_en'; for World, Tech/AI, "
            "Science, Business/Finance, or Astronomy use 'world' (or 'en'). "
            "Summarize conversationally (3–4 highlights first, without reading raw URLs) and invite the user to ask "
            "about any story — use news_search to pull deeper coverage whenever they want to discuss a story further."
        )
    try:
        w = await asyncio.wait_for(
            weather_service.get_weather(
                settings.weather_lat,
                settings.weather_lon,
                settings.weather_city,
                allow_stale=True,
            ),
            timeout=1.5,
        )
        cur = w["current"]
        context_lines.append(
            f"Current weather in {settings.weather_city}: {cur['temp']}°C (feels like {cur['feels_like']}°C), "
            f"{cur['description']}, humidity {cur['humidity']}%, wind {cur['wind_kmh']} km/h, "
            f"today's range {cur['temp_min']}°C to {cur['temp_max']}°C."
        )
        astro = w.get("astronomy")
        if astro:
            context_lines.append(
                f"Sun today: sunrise {astro['sunrise']}, sunset {astro['sunset']} (daylight {astro['daylight']}). "
                f"Moon today: {astro['moon_phase_name']} ({astro['moon_illumination']}% illuminated), "
                f"moonrise {astro['moonrise']}, moonset {astro['moonset']}."
            )
    except Exception:
        pass
    return "\n".join(context_lines)


async def handle_live_websocket(
    ws: WebSocket,
    settings: Settings,
    camera: CameraManager,
    weather_service: WeatherService,
    tv: SamsungTVController | None = None,
    roomba: RoombaController | None = None,
    news: GoogleNewsService | None = None,
) -> None:
    await ws.accept()

    async def send_json(payload: dict[str, Any]) -> None:
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            pass

    if not settings.gemini_api_key:
        await send_json({
            "type": "status",
            "state": "error",
            "message": "GEMINI_API_KEY is not configured. Tap Settings (gear icon) to add your Google AI Studio API key.",
        })
        await ws.close()
        return

    # Wait for initial start message from browser
    video_enabled = False
    initial_prompt: str | None = None
    try:
        raw_start = await asyncio.wait_for(ws.receive_text(), timeout=15.0)
        start_msg = json.loads(raw_start)
        if start_msg.get("type") == "start":
            video_enabled = bool(start_msg.get("video", False))
            initial_prompt = start_msg.get("prompt")
    except Exception as exc:
        logger.warning("Failed waiting for start frame: %s", exc)
        await ws.close()
        return

    persona_name = settings.persona_meta()["short_name"]
    await send_json({"type": "status", "state": "connecting", "message": f"Connecting to {persona_name}..."})

    camera_acquired = False
    if video_enabled:
        camera.acquire()
        camera_acquired = True

    stop_event = asyncio.Event()
    video_flag = {"enabled": video_enabled}

    try:
        system_instruction = await build_system_instruction(settings, weather_service, tv, roomba, news)

        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options={"api_version": "v1beta"},
        )

        config_kwargs: dict[str, Any] = dict(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=settings.gemini_voice
                    )
                )
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=ALLOWED_LANGUAGES,
            ),
            output_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=ALLOWED_LANGUAGES,
            ),
            system_instruction=types.Content(
                parts=[types.Part(text=system_instruction)]
            ),
        )
        tools = build_smart_home_tools(tv, roomba, news)
        if tools:
            config_kwargs["tools"] = tools

        config = types.LiveConnectConfig(**config_kwargs)

        async with client.aio.live.connect(model=settings.gemini_model, config=config) as session:
            await send_json({"type": "status", "state": "connected", "message": "Listening..."})

            if initial_prompt:
                await send_json({"type": "transcript", "role": "user", "text": initial_prompt, "final": True})
                await session.send_realtime_input(text=initial_prompt)

            async def client_to_gemini() -> None:
                nonlocal camera_acquired
                try:
                    while not stop_event.is_set():
                        msg = await ws.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if "bytes" in msg and msg["bytes"]:
                            await session.send_realtime_input(
                                audio=types.Blob(
                                    data=msg["bytes"],
                                    mime_type="audio/pcm;rate=16000",
                                )
                            )
                        elif "text" in msg and msg["text"]:
                            data = json.loads(msg["text"])
                            mtype = data.get("type")
                            if mtype == "stop":
                                break
                            elif mtype == "video_toggle":
                                want = bool(data.get("enabled", False))
                                video_flag["enabled"] = want
                                if want and not camera_acquired:
                                    camera.acquire()
                                    camera_acquired = True
                                elif not want and camera_acquired:
                                    camera.release()
                                    camera_acquired = False
                            elif mtype == "text" and data.get("text"):
                                txt = str(data["text"]).strip()
                                if txt:
                                    await send_json({"type": "transcript", "role": "user", "text": txt, "final": True})
                                    await session.send_realtime_input(text=txt)
                except WebSocketDisconnect:
                    pass
                except Exception as exc:
                    logger.warning("client_to_gemini ended: %s", exc)
                finally:
                    stop_event.set()

            async def camera_to_gemini() -> None:
                try:
                    while not stop_event.is_set():
                        if video_flag["enabled"]:
                            jpeg = await asyncio.to_thread(camera.get_latest_jpeg, 0.5)
                            if jpeg and not stop_event.is_set():
                                await session.send_realtime_input(
                                    video=types.Blob(data=jpeg, mime_type="image/jpeg")
                                )
                        await asyncio.sleep(1.0)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.warning("camera_to_gemini ended: %s", exc)

            async def gemini_to_client() -> None:
                sent_rate: int | None = None
                try:
                    while not stop_event.is_set():
                        async for response in session.receive():
                            if stop_event.is_set():
                                break

                            # Handle Gemini Live function calls (Samsung TV, Roomba, etc.)
                            if response.tool_call and response.tool_call.function_calls:
                                fn_responses: list[types.FunctionResponse] = []
                                for fc in response.tool_call.function_calls:
                                    fn_name = fc.name or ""
                                    fn_args = dict(fc.args or {})
                                    logger.info("Gemini tool_call: %s(%s)", fn_name, fn_args)
                                    result, label, icon = await asyncio.to_thread(
                                        _execute_smart_home_tool, tv, roomba, news, settings, fn_name, fn_args
                                    )
                                    ok = bool(result.get("ok"))
                                    await send_json({
                                        "type": "tool",
                                        "ok": ok,
                                        "text": f"{icon} {label}" + ("" if ok else f" ({result.get('error', 'failed')})"),
                                    })
                                    fn_responses.append(
                                        types.FunctionResponse(
                                            id=fc.id,
                                            name=fn_name,
                                            response=result,
                                        )
                                    )
                                if fn_responses and not stop_event.is_set():
                                    await session.send_tool_response(function_responses=fn_responses)

                            sc = response.server_content
                            if sc is None:
                                continue

                            if sc.interrupted:
                                await send_json({"type": "interrupted"})

                            if sc.input_transcription and sc.input_transcription.text:
                                filtered_in = _filter_transcript(sc.input_transcription.text, strict_chunk=True)
                                if filtered_in:
                                    await send_json({
                                        "type": "transcript",
                                        "role": "user",
                                        "text": filtered_in,
                                        "final": False,
                                    })
                                else:
                                    logger.debug("Filtered non-EN/HE/RU input transcript: %r", sc.input_transcription.text)

                            if sc.output_transcription and sc.output_transcription.text:
                                filtered_out = _filter_transcript(sc.output_transcription.text, strict_chunk=False)
                                if filtered_out:
                                    await send_json({
                                        "type": "transcript",
                                        "role": "assistant",
                                        "text": filtered_out,
                                        "final": False,
                                    })
                                else:
                                    logger.debug("Filtered non-EN/HE/RU output transcript: %r", sc.output_transcription.text)

                            if sc.model_turn and sc.model_turn.parts:
                                for part in sc.model_turn.parts:
                                    if part.inline_data and part.inline_data.data:
                                        mime = part.inline_data.mime_type or "audio/pcm;rate=24000"
                                        rate = 24000
                                        if "rate=" in mime:
                                            try:
                                                rate = int(mime.split("rate=", 1)[1].split(";", 1)[0].strip())
                                            except ValueError:
                                                rate = 24000
                                        if sent_rate != rate:
                                            sent_rate = rate
                                            logger.info("Gemini audio format: %s (len=%d)", mime, len(part.inline_data.data))
                                            await send_json({"type": "audio_format", "rate": rate, "mime": mime})
                                        await ws.send_bytes(part.inline_data.data)
                                    elif part.text and not getattr(part, "thought", False):
                                        filtered_txt = _filter_transcript(part.text, strict_chunk=False)
                                        if filtered_txt:
                                            await send_json({
                                                "type": "transcript",
                                                "role": "assistant",
                                                "text": filtered_txt,
                                                "final": False,
                                            })
                                        else:
                                            logger.debug("Filtered non-EN/HE/RU assistant text: %r", part.text)

                            if sc.turn_complete:
                                await send_json({"type": "turn_complete"})
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    if not stop_event.is_set():
                        logger.exception("gemini_to_client error: %s", exc)
                        await send_json({"type": "status", "state": "error", "message": str(exc)})
                finally:
                    stop_event.set()

            tasks = [
                asyncio.create_task(client_to_gemini()),
                asyncio.create_task(camera_to_gemini()),
                asyncio.create_task(gemini_to_client()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            stop_event.set()
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    except Exception as exc:
        logger.exception("Gemini Live session error: %s", exc)
        await send_json({"type": "status", "state": "error", "message": f"Connection error: {exc}"})
    finally:
        if camera_acquired:
            camera.release()
        await send_json({"type": "status", "state": "closed", "message": "Session ended"})
        try:
            await ws.close()
        except Exception:
            pass
