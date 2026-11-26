from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .camera import CameraManager
from .config import (
    AVAILABLE_MODELS,
    AVAILABLE_SPEAKING_PACES,
    AVAILABLE_VOICES,
    BASE_DIR,
    PERSONAS,
    load_settings,
    save_settings,
)
from .gemini_live import handle_live_websocket
from .news import GoogleNewsService
from .roomba import RoombaController
from .samsung_tv import SamsungTVController
from .weather import WeatherService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

settings = load_settings()
weather_service = WeatherService(cache_ttl_seconds=600)
camera_manager = CameraManager(
    get_rotation=lambda: settings.camera_rotate,
    get_hflip=lambda: settings.camera_hflip,
    fps=6.0,
    jpeg_quality=68,
)
tv_controller = SamsungTVController(
    host=settings.samsung_tv_ip,
    mac=settings.samsung_tv_mac,
    token_file=BASE_DIR / "tv_token.txt",
)
roomba_controller = RoombaController(
    host=settings.roomba_ip,
    blid=settings.roomba_blid,
    password=settings.roomba_password,
)
news_service = GoogleNewsService(cache_ttl_seconds=300)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    camera_manager.force_stop()
    news_service.close()
    await weather_service.aclose()


app = FastAPI(title="Gemini Smart Display", lifespan=lifespan)


@app.middleware("http")
async def add_static_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/") and response.status_code == 200:
        if "v" in request.query_params:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "public, max-age=86400"
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _wpctl_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _get_volume() -> dict[str, Any]:
    try:
        out = subprocess.check_output(
            ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
            env=_wpctl_env(),
            text=True,
            timeout=2.0,
        ).strip()
        m = re.search(r"([0-9]+\.[0-9]+)", out)
        level = int(round(float(m.group(1)) * 100)) if m else 50
        muted = "[MUTED]" in out
        return {"volume": max(0, min(100, level)), "muted": muted}
    except Exception as exc:
        logger.debug("wpctl get-volume failed: %s", exc)
        return {"volume": 50, "muted": False}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/weather")
async def api_weather(refresh: bool = False) -> dict[str, Any]:
    try:
        return await weather_service.get_weather(
            settings.weather_lat,
            settings.weather_lon,
            settings.weather_city,
            force=refresh,
        )
    except Exception as exc:
        logger.warning("Weather fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Weather unavailable: {exc}")


@app.get("/api/geocode")
async def api_geocode(q: str = Query(..., min_length=2)) -> dict[str, Any]:
    try:
        results = await weather_service.search_city(q)
        return {"results": results}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/settings")
async def api_get_settings() -> dict[str, Any]:
    return settings.to_public_dict()


class SettingsUpdate(BaseModel):
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    gemini_voice: str | None = None
    speaking_pace: str | None = None
    weather_city: str | None = None
    weather_lat: float | None = None
    weather_lon: float | None = None
    camera_rotate: int | None = None
    camera_hflip: bool | None = None
    news_topics: str | None = None
    persona: str | None = None
    system_prompt: str | None = None


@app.post("/api/settings")
async def api_update_settings(body: SettingsUpdate) -> dict[str, Any]:
    global settings
    if body.gemini_api_key is not None and body.gemini_api_key.strip() != "":
        settings.gemini_api_key = body.gemini_api_key.strip()
    if body.gemini_model is not None and body.gemini_model.strip():
        settings.gemini_model = body.gemini_model.strip()
    if body.gemini_voice is not None and body.gemini_voice in AVAILABLE_VOICES:
        settings.gemini_voice = body.gemini_voice
    if body.speaking_pace is not None and body.speaking_pace in AVAILABLE_SPEAKING_PACES:
        settings.speaking_pace = body.speaking_pace
    if body.weather_city is not None and body.weather_city.strip():
        city_name = body.weather_city.strip()
        if body.weather_lat is not None and body.weather_lon is not None:
            settings.weather_city = city_name
            settings.weather_lat = body.weather_lat
            settings.weather_lon = body.weather_lon
        elif city_name.lower() != settings.weather_city.lower():
            matches = await weather_service.search_city(city_name)
            if not matches:
                raise HTTPException(status_code=404, detail=f"City not found: {city_name}")
            top = matches[0]
            settings.weather_city = top["name"]
            settings.weather_lat = float(top["lat"])
            settings.weather_lon = float(top["lon"])
    else:
        if body.weather_lat is not None:
            settings.weather_lat = body.weather_lat
        if body.weather_lon is not None:
            settings.weather_lon = body.weather_lon
    if body.camera_rotate is not None and body.camera_rotate in (0, 90, 180, 270):
        settings.camera_rotate = body.camera_rotate
    if body.camera_hflip is not None:
        settings.camera_hflip = body.camera_hflip
    if body.news_topics is not None:
        cleaned_topics = ", ".join(t.strip() for t in body.news_topics.split(",") if t.strip())
        if cleaned_topics:
            settings.news_topics = cleaned_topics
    if body.persona is not None and body.persona in PERSONAS:
        settings.persona = body.persona
        preset_prompt = PERSONAS[body.persona]["prompt"]
        if preset_prompt and (body.system_prompt is None or not body.system_prompt.strip()):
            settings.system_prompt = preset_prompt
    if body.system_prompt is not None and body.system_prompt.strip():
        cleaned_prompt = " ".join(body.system_prompt.strip().splitlines())
        settings.system_prompt = cleaned_prompt
        if settings.persona in ("sarah", "standard") and cleaned_prompt != PERSONAS[settings.persona]["prompt"]:
            settings.persona = "custom"

    save_settings(settings)
    weather_service.invalidate()
    return settings.to_public_dict()


@app.get("/api/volume")
async def api_get_volume() -> dict[str, Any]:
    return await asyncio.to_thread(_get_volume)


class VolumeUpdate(BaseModel):
    volume: int | None = None
    delta: int | None = None
    mute: bool | None = None


@app.post("/api/volume")
async def api_set_volume(body: VolumeUpdate) -> dict[str, Any]:
    def _apply() -> dict[str, Any]:
        env = _wpctl_env()
        if body.mute is not None:
            subprocess.run(
                ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if body.mute else "0"],
                env=env,
                timeout=2.0,
                check=False,
            )
        if body.delta is not None and body.delta != 0:
            sign = "+" if body.delta > 0 else "-"
            step = min(100, abs(body.delta))
            subprocess.run(
                ["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{step}%{sign}"],
                env=env,
                timeout=2.0,
                check=False,
            )
        elif body.volume is not None:
            target = max(0, min(100, body.volume))
            subprocess.run(
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{target}%"],
                env=env,
                timeout=2.0,
                check=False,
            )
        return _get_volume()

    return await asyncio.to_thread(_apply)


@app.get("/api/camera/stream")
async def api_camera_stream() -> StreamingResponse:
    async def frame_gen():
        camera_manager.acquire()
        last_seq = 0
        try:
            while True:
                last_seq, jpeg = await asyncio.to_thread(camera_manager.wait_next_jpeg, last_seq, 1.0)
                if jpeg:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
                        + jpeg
                        + b"\r\n"
                    )
                else:
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        finally:
            camera_manager.release()

    return StreamingResponse(
        frame_gen(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/api/camera/snapshot")
async def api_camera_snapshot() -> Response:
    camera_manager.acquire()
    try:
        jpeg = await asyncio.to_thread(camera_manager.get_latest_jpeg, 3.0)
    finally:
        camera_manager.release()
    if not jpeg:
        raise HTTPException(status_code=503, detail=camera_manager.last_error or "Camera unavailable")
    return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/tv/status")
async def api_tv_status() -> dict[str, Any]:
    return await asyncio.to_thread(tv_controller.get_status)


@app.get("/api/roomba/status")
async def api_roomba_status() -> dict[str, Any]:
    return await asyncio.to_thread(roomba_controller.get_status)


@app.get("/api/news/briefing")
async def api_news_briefing(language: str = "en", per_topic: int = 3) -> dict[str, Any]:
    return await asyncio.to_thread(
        news_service.get_briefing,
        settings.news_topics_list(),
        language,
        per_topic,
    )


@app.get("/api/news/search")
async def api_news_search(
    q: str = Query(..., min_length=2),
    language: str = "en",
    days: int = 7,
    limit: int = 6,
) -> dict[str, Any]:
    return await asyncio.to_thread(news_service.search_news, q, language, days, limit)


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket) -> None:
    await handle_live_websocket(
        ws, settings, camera_manager, weather_service, tv_controller, roomba_controller, news_service
    )
