from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from astral import Observer
from astral.moon import moonrise, moonset, phase as moon_phase
from astral.sun import sun

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


def _moon_phase_name(phase_val: float) -> str:
    # astral.moon.phase returns 0..27.99 (0=New, 7=First Quarter, 14=Full, 21=Last Quarter)
    p = phase_val % 28.0
    if p < 1.2 or p >= 26.8:
        return "New Moon"
    if p < 6.0:
        return "Waxing Crescent"
    if p < 8.2:
        return "First Quarter"
    if p < 13.0:
        return "Waxing Gibbous"
    if p < 15.2:
        return "Full Moon"
    if p < 20.0:
        return "Waning Gibbous"
    if p < 22.2:
        return "Last Quarter"
    return "Waning Crescent"


def compute_astronomy(lat: float, lon: float, tz_name: str | None = None) -> dict[str, Any]:
    try:
        tz = ZoneInfo(tz_name) if tz_name else datetime.now().astimezone().tzinfo
    except Exception:
        tz = datetime.now().astimezone().tzinfo

    now = datetime.now(tz)
    today = now.date()
    obs = Observer(latitude=lat, longitude=lon)

    sunrise_str = "--:--"
    sunset_str = "--:--"
    daylight_str = ""
    try:
        s = sun(obs, date=today, tzinfo=tz)
        sr = s.get("sunrise")
        ss = s.get("sunset")
        if sr:
            sunrise_str = sr.strftime("%H:%M")
        if ss:
            sunset_str = ss.strftime("%H:%M")
        if sr and ss and ss > sr:
            mins = int((ss - sr).total_seconds() // 60)
            daylight_str = f"{mins // 60}h {mins % 60:02d}m"
    except Exception:
        pass

    moonrise_str = "--:--"
    try:
        mr = moonrise(obs, date=today, tzinfo=tz)
        if mr:
            moonrise_str = mr.strftime("%H:%M")
    except Exception:
        pass

    moonset_str = "--:--"
    try:
        ms = moonset(obs, date=today, tzinfo=tz)
        if ms:
            moonset_str = ms.strftime("%H:%M")
    except Exception:
        pass

    ph = float(moon_phase(today))
    frac = (ph % 28.0) / 28.0
    illum = int(round((1.0 - math.cos(frac * 2.0 * math.pi)) * 50.0))

    return {
        "sunrise": sunrise_str,
        "sunset": sunset_str,
        "daylight": daylight_str,
        "moonrise": moonrise_str,
        "moonset": moonset_str,
        "moon_phase_value": round(ph, 2),
        "moon_phase_frac": round(frac, 4),
        "moon_phase_name": _moon_phase_name(ph),
        "moon_illumination": illum,
    }


class WeatherService:
    def __init__(self, cache_ttl_seconds: int = 600) -> None:
        self._ttl = cache_ttl_seconds
        self._cache: dict[tuple[float, float, int], tuple[float, dict[str, Any]]] = {}
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=12.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def invalidate(self) -> None:
        self._cache.clear()

    async def get_weather(
        self,
        lat: float,
        lon: float,
        city: str,
        force: bool = False,
        allow_stale: bool = False,
        forecast_days: int = 5,
    ) -> dict[str, Any]:
        days = max(1, min(7, int(forecast_days or 5)))
        key = (round(lat, 4), round(lon, 4), days)
        now = time.monotonic()
        cached = self._cache.get(key)
        if not force and cached is not None:
            cached_ts, cached_val = cached
            if (now - cached_ts) < self._ttl or allow_stale:
                if cached_val.get("city") == city:
                    return cached_val
                return {**cached_val, "city": city}

        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
            "timezone": "auto",
            "forecast_days": days,
        }

        raw = None
        last_exc: Exception | None = None
        client = self._get_client()
        for attempt in range(2):
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                raw = resp.json()
                break
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(1.5)
        if raw is None:
            if cached is not None:
                return {**cached[1], "city": city}
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

        for i in range(min(len(dates), days)):
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

        tz_name = raw.get("timezone")
        try:
            local_now = datetime.now(ZoneInfo(tz_name)) if tz_name else datetime.now().astimezone()
            local_time_str = local_now.strftime("%A, %H:%M")
        except Exception:
            local_time_str = datetime.now().strftime("%A, %H:%M")

        astro = compute_astronomy(lat, lon, tz_name)

        result = {
            "city": city,
            "lat": lat,
            "lon": lon,
            "timezone": tz_name or "",
            "local_time": local_time_str,
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
            "forecast": forecast[1:days],
            "daily_all": forecast,
            "astronomy": astro,
        }

        self._cache[key] = (now, result)
        if len(self._cache) > 32:
            oldest_key = min(self._cache.items(), key=lambda kv: kv[1][0])[0]
            self._cache.pop(oldest_key, None)
        return result

    async def search_city(self, query: str, count: int = 6) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if len(q) < 2:
            return []
        lang = "en"
        if any("\u0590" <= ch <= "\u05FF" for ch in q):
            lang = "he"
        elif any("\u0400" <= ch <= "\u04FF" for ch in q):
            lang = "ru"
        url = "https://geocoding-api.open-meteo.com/v1/search"
        params = {
            "name": q,
            "count": max(1, min(20, int(count or 6))),
            "language": lang,
            "format": "json",
        }
        resp = await self._get_client().get(url, params=params, timeout=8.0)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results and lang != "en":
            params["language"] = "en"
            resp = await self._get_client().get(url, params=params, timeout=8.0)
            resp.raise_for_status()
            results = resp.json().get("results", [])
        out = []
        for item in results:
            name = item.get("name", "")
            admin = item.get("admin1", "")
            country = item.get("country", "")
            country_code = item.get("country_code", "")
            label = ", ".join(p for p in [name, admin, country] if p)
            out.append({
                "name": name,
                "admin1": admin,
                "country": country,
                "country_code": country_code,
                "label": label,
                "lat": item.get("latitude"),
                "lon": item.get("longitude"),
            })
        return out

    async def get_weather_by_location(
        self,
        location: str,
        country: str | None = None,
        days: int = 5,
    ) -> dict[str, Any]:
        raw_loc = (location or "").strip()
        if not raw_loc:
            return {"ok": False, "error": "Location name is required."}

        # Open-Meteo geocoding matches city names best without trailing ", Country/State"
        city_query = raw_loc
        qualifier = (country or "").strip()
        if "," in raw_loc:
            parts = [p.strip() for p in raw_loc.split(",") if p.strip()]
            if parts:
                city_query = parts[0]
                if not qualifier and len(parts) > 1:
                    qualifier = " ".join(parts[1:])

        try:
            matches = await self.search_city(city_query, count=10)
            if not matches and city_query != raw_loc:
                matches = await self.search_city(raw_loc, count=10)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to look up location '{raw_loc}': {exc}"}

        if not matches:
            return {"ok": False, "error": f"Location not found: {raw_loc}"}

        chosen = matches[0]
        if qualifier:
            q_low = qualifier.lower()
            aliases = {
                "uk": {"gb", "united kingdom"},
                "usa": {"us", "united states"},
                "u.s.": {"us", "united states"},
                "u.s.a.": {"us", "united states"},
                "uae": {"ae", "united arab emirates"},
            }
            targets = aliases.get(q_low, {q_low})
            for m in matches:
                fields = [
                    (m.get("country") or "").lower(),
                    (m.get("country_code") or "").lower(),
                    (m.get("admin1") or "").lower(),
                ]
                if any(t == f or (len(t) > 2 and t in f) for t in targets for f in fields if f):
                    chosen = m
                    break

        display_label = chosen["label"] or chosen["name"]
        try:
            w = await self.get_weather(
                float(chosen["lat"]),
                float(chosen["lon"]),
                display_label,
                forecast_days=days,
            )
        except Exception as exc:
            return {"ok": False, "error": f"Failed to fetch weather for {display_label}: {exc}"}

        cur = w["current"]
        astro = w.get("astronomy") or {}
        daily_out = [
            {
                "day": d["day"],
                "date": d["date"],
                "description": d["description"],
                "temp_max_c": d["temp_max"],
                "temp_min_c": d["temp_min"],
                "precip_prob_pct": d["precip_prob"],
            }
            for d in w.get("daily_all", [])
        ]
        return {
            "ok": True,
            "location": display_label,
            "local_time": w.get("local_time", ""),
            "timezone": w.get("timezone", ""),
            "current": {
                "temp_c": cur["temp"],
                "feels_like_c": cur["feels_like"],
                "description": cur["description"],
                "humidity_pct": cur["humidity"],
                "wind_kmh": cur["wind_kmh"],
                "is_day": cur["is_day"],
                "today_high_c": cur["temp_max"],
                "today_low_c": cur["temp_min"],
            },
            "forecast": daily_out,
            "sun": {
                "sunrise": astro.get("sunrise"),
                "sunset": astro.get("sunset"),
                "daylight": astro.get("daylight"),
            },
        }
