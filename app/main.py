from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .camera import CameraManager
from .config import AVAILABLE_MODELS, AVAILABLE_VOICES, load_settings, save_settings
from .gemini_live import handle_live_websocket
from .weather import WeatherService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

settings = load_settings()
weather_service = WeatherService(cache_ttl_seconds=600)
camera_manager = CameraManager(get_rotation=lambda: settings.camera_rotate, fps=12.0, jpeg_quality=75)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    camera_manager.force_stop()


app = FastAPI(title="Gemini Smart Display", lifespan=lifespan)
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
    weather_city: str | None = None
    weather_lat: float | None = None
    weather_lon: float | None = None
    camera_rotate: int | None = None
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
    if body.weather_city is not None and body.weather_city.strip():
        settings.weather_city = body.weather_city.strip()
    if body.weather_lat is not None:
        settings.weather_lat = body.weather_lat
    if body.weather_lon is not None:
        settings.weather_lon = body.weather_lon
    if body.camera_rotate is not None and body.camera_rotate in (0, 90, 180, 270):
        settings.camera_rotate = body.camera_rotate
    if body.system_prompt is not None and body.system_prompt.strip():
        settings.system_prompt = body.system_prompt.strip()

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
        if body.delta is not None:
            cur = _get_volume()["volume"]
            target = max(0, min(100, cur + body.delta))
            subprocess.run(
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{target}%"],
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


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket) -> None:
    await handle_live_websocket(ws, settings, camera_manager, weather_service)
