from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any
import httpx

WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("Clear sky", "clear"),
    1: ("Mainly clear", "mostly_clear"),
    2: ("Partly cloudy", "partly_cloudy"),
    3: ("Overcast", "cloudy"),
    45: ("Fog", "fog"),
    48: ("Rime fog", "fog"),
    51: ("Light drizzle", "drizzle"),
    53: ("Drizzle", "drizzle"),
    55: ("Dense drizzle", "drizzle"),
    56: ("Freezing drizzle", "drizzle"),
    57: ("Dense freezing drizzle", "drizzle"),
    61: ("Light rain", "rain"),
    63: ("Moderate rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"),
    67: ("Heavy freezing rain", "rain"),
    71: ("Light snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"),
    80: ("Light showers", "rain"),
    81: ("Rain showers", "rain"),
    82: ("Heavy showers", "rain"),
    85: ("Snow showers", "snow"),
    86: ("Heavy snow showers", "snow"),
    95: ("Thunderstorm", "storm"),
    96: ("Thunderstorm & hail", "storm"),
    99: ("Severe thunderstorm", "storm"),
}


class WeatherService:
    def __init__(self, cache_ttl_seconds: int = 600) -> None:
        self._ttl = cache_ttl_seconds
        self._cache: dict[str, Any] | None = None
        self._cache_key: tuple[float, float, str] | None = None
        self._cache_ts: float = 0.0

    def invalidate(self) -> None:
        self._cache = None
        self._cache_ts = 0.0

    async def get_weather(self, lat: float, lon: float, city: str, force: bool = False) -> dict[str, Any]:
        key = (round(lat, 4), round(lon, 4), city)
        now = time.monotonic()
        if not force and self._cache and self._cache_key == key and (now - self._cache_ts) < self._ttl:
            return self._cache

        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
            "timezone": "auto",
            "forecast_days": 5,
        }

        raw = None
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=12.0) as client:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    raw = resp.json()
                    break
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(1.5)
        if raw is None:
            if self._cache and self._cache_key == key:
                return self._cache
            raise last_exc or RuntimeError("Weather fetch failed")

        cur = raw.get("current", {})
        daily = raw.get("daily", {})

        code = int(cur.get("weather_code", 0))
        desc, icon = WMO_CODES.get(code, ("Unknown", "cloudy"))
        is_day = bool(cur.get("is_day", 1))
        if not is_day and icon in ("clear", "mostly_clear", "partly_cloudy"):
            icon = f"{icon}_night"

        forecast = []
        dates = daily.get("time", [])
        codes = daily.get("weather_code", [])
        maxs = daily.get("temperature_2m_max", [])
        mins = daily.get("temperature_2m_min", [])
        pops = daily.get("precipitation_probability_max", [])

        for i in range(min(len(dates), 5)):
            d_code = int(codes[i]) if i < len(codes) and codes[i] is not None else 0
            d_desc, d_icon = WMO_CODES.get(d_code, ("Unknown", "cloudy"))
            try:
                dt = datetime.strptime(dates[i], "%Y-%m-%d")
                day_label = "Today" if i == 0 else dt.strftime("%a")
            except ValueError:
                day_label = dates[i]

            forecast.append({
                "date": dates[i],
                "day": day_label,
                "code": d_code,
                "description": d_desc,
                "icon": d_icon,
                "temp_max": round(maxs[i]) if i < len(maxs) and maxs[i] is not None else None,
                "temp_min": round(mins[i]) if i < len(mins) and mins[i] is not None else None,
                "precip_prob": int(pops[i]) if i < len(pops) and pops[i] is not None else 0,
            })

        result = {
            "city": city,
            "lat": lat,
            "lon": lon,
            "updated_at": datetime.now().strftime("%H:%M"),
            "current": {
                "temp": round(cur.get("temperature_2m", 0)),
                "feels_like": round(cur.get("apparent_temperature", 0)),
                "humidity": int(cur.get("relative_humidity_2m", 0)),
                "wind_kmh": round(cur.get("wind_speed_10m", 0)),
                "code": code,
                "description": desc,
                "icon": icon,
                "is_day": is_day,
                "temp_max": forecast[0]["temp_max"] if forecast else None,
                "temp_min": forecast[0]["temp_min"] if forecast else None,
            },
            "forecast": forecast[1:5],
        }

        self._cache = result
        self._cache_key = key
        self._cache_ts = now
        return result

    async def search_city(self, query: str) -> list[dict[str, Any]]:
        if not query or len(query.strip()) < 2:
            return []
        url = "https://geocoding-api.open-meteo.com/v1/search"
        params = {"name": query.strip(), "count": 6, "language": "en", "format": "json"}
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        out = []
        for item in data.get("results", []):
            name = item.get("name", "")
            admin = item.get("admin1", "")
            country = item.get("country", "")
            label = ", ".join(p for p in [name, admin, country] if p)
            out.append({
                "name": name,
                "label": label,
                "lat": item.get("latitude"),
                "lon": item.get("longitude"),
            })
        return out
