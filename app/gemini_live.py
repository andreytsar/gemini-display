from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

from .camera import CameraManager
from .config import Settings
from .weather import WeatherService

logger = logging.getLogger(__name__)

ALLOWED_LANGUAGES = ["en-US", "he-IL", "ru-RU"]

# Matches any letter outside Basic/Extended Latin, Hebrew, and Cyrillic
_DISALLOWED_SCRIPT_RE = re.compile(
    r"[^\x00-\x7F\u00C0-\u024F\u0400-\u04FF\u0590-\u05FF\u2000-\u206F\s]"
)


def _is_allowed_transcript(text: str) -> bool:
    if not text or not text.strip():
        return False
    # Reject chunks dominated by scripts outside English/Hebrew/Russian
    disallowed = len(_DISALLOWED_SCRIPT_RE.findall(text))
    return disallowed == 0


async def build_system_instruction(settings: Settings, weather_service: WeatherService) -> str:
    now = datetime.now()
    context_lines = [
        settings.system_prompt,
        "Strict language rule: The user ONLY speaks English, Hebrew (עברית), or Russian (Русский). "
        "Never interpret unclear audio or background noise as any other language. "
        "Always respond naturally in whichever of those three languages (English, Hebrew, or Russian) the user spoke, "
        "switching seamlessly if the user switches between them.",
        f"Current local date and time: {now.strftime('%A, %d %B %Y, %H:%M')}.",
        f"Location: {settings.weather_city}.",
    ]
    try:
        w = await weather_service.get_weather(settings.weather_lat, settings.weather_lon, settings.weather_city)
        cur = w["current"]
        context_lines.append(
            f"Current weather in {settings.weather_city}: {cur['temp']}°C (feels like {cur['feels_like']}°C), "
            f"{cur['description']}, humidity {cur['humidity']}%, wind {cur['wind_kmh']} km/h, "
            f"today's range {cur['temp_min']}°C to {cur['temp_max']}°C."
        )
    except Exception:
        pass
    return "\n".join(context_lines)


async def handle_live_websocket(
    ws: WebSocket,
    settings: Settings,
    camera: CameraManager,
    weather_service: WeatherService,
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

    await send_json({"type": "status", "state": "connecting", "message": f"Connecting to {settings.gemini_model}..."})

    camera_acquired = False
    if video_enabled:
        camera.acquire()
        camera_acquired = True

    stop_event = asyncio.Event()
    video_flag = {"enabled": video_enabled}

    try:
        system_instruction = await build_system_instruction(settings, weather_service)

        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options={"api_version": "v1beta"},
        )

        config = types.LiveConnectConfig(
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
                            sc = response.server_content
                            if sc is None:
                                continue

                            if sc.interrupted:
                                await send_json({"type": "interrupted"})

                            if sc.input_transcription and sc.input_transcription.text:
                                if _is_allowed_transcript(sc.input_transcription.text):
                                    await send_json({
                                        "type": "transcript",
                                        "role": "user",
                                        "text": sc.input_transcription.text,
                                        "final": False,
                                    })
                                else:
                                    logger.debug("Filtered non-EN/HE/RU input transcript: %r", sc.input_transcription.text)

                            if sc.output_transcription and sc.output_transcription.text:
                                await send_json({
                                    "type": "transcript",
                                    "role": "assistant",
                                    "text": sc.output_transcription.text,
                                    "final": False,
                                })

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
                                        await send_json({
                                            "type": "transcript",
                                            "role": "assistant",
                                            "text": part.text,
                                            "final": False,
                                        })

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
