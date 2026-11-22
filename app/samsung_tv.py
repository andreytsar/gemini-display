from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from samsungtvws import SamsungTVWS
from wakeonlan import send_magic_packet

logger = logging.getLogger(__name__)

# Well-known Samsung Tizen App IDs (with EN / HE / RU aliases)
KNOWN_APPS: dict[str, tuple[str, str]] = {
    "youtube": ("111299001912", "YouTube"),
    "יוטיוב": ("111299001912", "YouTube"),
    "ютуб": ("111299001912", "YouTube"),
    "netflix": ("3201907018807", "Netflix"),
    "נטפליקס": ("3201907018807", "Netflix"),
    "нетфликс": ("3201907018807", "Netflix"),
    "spotify": ("3201606009684", "Spotify"),
    "ספוטיפיי": ("3201606009684", "Spotify"),
    "спотифай": ("3201606009684", "Spotify"),
    "prime": ("3201910019365", "Prime Video"),
    "prime video": ("3201910019365", "Prime Video"),
    "amazon": ("3201910019365", "Prime Video"),
    "פריים": ("3201910019365", "Prime Video"),
    "прайм": ("3201910019365", "Prime Video"),
    "disney": ("3201901017640", "Disney+"),
    "disney+": ("3201901017640", "Disney+"),
    "דיסני": ("3201901017640", "Disney+"),
    "дисней": ("3201901017640", "Disney+"),
    "apple tv": ("3201807016597", "Apple TV"),
    "appletv": ("3201807016597", "Apple TV"),
    "אפל": ("3201807016597", "Apple TV"),
    "эппл": ("3201807016597", "Apple TV"),
    "plex": ("3201512006963", "Plex"),
    "פלקס": ("3201512006963", "Plex"),
    "плекс": ("3201512006963", "Plex"),
    "browser": ("org.tizen.browser", "Internet Browser"),
    "internet": ("org.tizen.browser", "Internet Browser"),
    "דפדפן": ("org.tizen.browser", "Internet Browser"),
    "браузер": ("org.tizen.browser", "Internet Browser"),
}

ACTION_KEYS: dict[str, str] = {
    "play": "KEY_PLAY",
    "pause": "KEY_PAUSE",
    "stop": "KEY_STOP",
    "rewind": "KEY_REWIND",
    "fast_forward": "KEY_FF",
    "back": "KEY_RETURN",
    "return": "KEY_RETURN",
    "home": "KEY_HOME",
    "exit": "KEY_EXIT",
    "enter": "KEY_ENTER",
    "ok": "KEY_ENTER",
    "up": "KEY_UP",
    "down": "KEY_DOWN",
    "left": "KEY_LEFT",
    "right": "KEY_RIGHT",
    "channel_up": "KEY_CHUP",
    "channel_down": "KEY_CHDOWN",
    "prev_channel": "KEY_PRECH",
    "source": "KEY_SOURCE",
    "hdmi": "KEY_HDMI",
    "hdmi1": "KEY_HDMI1",
    "hdmi2": "KEY_HDMI2",
    "hdmi3": "KEY_HDMI3",
    "hdmi4": "KEY_HDMI4",
    "tv": "KEY_TV",
    "menu": "KEY_MENU",
    "guide": "KEY_GUIDE",
    "info": "KEY_INFO",
    "ambient": "KEY_AMBIENT",
}


class SamsungTVController:
    def __init__(self, host: str, mac: str, token_file: Path, name: str = "Gemini Display") -> None:
        self.host = host.strip()
        self.mac = mac.strip()
        self.token_file = token_file
        self.name = name
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.host)

    @property
    def paired(self) -> bool:
        return self.token_file.exists() and self.token_file.stat().st_size > 0

    def _connect(self, timeout: int = 5) -> SamsungTVWS:
        tv = SamsungTVWS(
            host=self.host,
            port=8002,
            token_file=str(self.token_file),
            name=self.name,
            timeout=timeout,
        )
        tv.open()
        if self.token_file.exists():
            try:
                os.chmod(self.token_file, 0o600)
            except OSError:
                pass
        return tv

    def get_status(self) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "error": "Samsung TV IP not configured"}
        try:
            r = httpx.get(f"http://{self.host}:8001/api/v2/", timeout=2.5)
            r.raise_for_status()
            data = r.json()
            dev = data.get("device", {})
            return {
                "ok": True,
                "reachable": True,
                "name": dev.get("name", "Samsung TV"),
                "model": dev.get("modelName", ""),
                "power_state": dev.get("PowerState", "unknown"),
                "paired": self.paired,
            }
        except Exception as exc:
            return {
                "ok": True,
                "reachable": False,
                "power_state": "off",
                "paired": self.paired,
                "note": str(exc),
            }

    def _broadcast_ip(self) -> str | None:
        parts = self.host.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
        return None

    def _send_wol(self, repeats: int = 2) -> None:
        if not self.mac:
            return
        targets: list[str] = []
        bcast = self._broadcast_ip()
        if bcast:
            targets.append(bcast)
        if self.host:
            targets.append(self.host)
        targets.append("255.255.255.255")

        for _ in range(repeats):
            for target in targets:
                for port in (9, 7):
                    try:
                        send_magic_packet(self.mac, ip_address=target, port=port)
                    except Exception as exc:
                        logger.debug("WoL packet to %s:%d failed: %s", target, port, exc)
            time.sleep(0.15)

    def _wake_tv(self) -> dict[str, Any]:
        st = self.get_status()
        cur = st.get("power_state", "unknown")
        reachable = bool(st.get("reachable", False))

        if cur == "on":
            return {"ok": True, "message": "The Samsung TV is already on.", "power_state": "on"}

        # Warm standby: WSS port 8002 stays open and KEY_POWER wakes the TV immediately
        if reachable and cur == "standby":
            res = self.send_key("KEY_POWER")
            if res.get("ok"):
                for _ in range(4):
                    time.sleep(0.75)
                    new_state = self.get_status().get("power_state", "unknown")
                    if new_state == "on":
                        return {
                            "ok": True,
                            "message": "Turned on the Samsung TV.",
                            "power_state": "on",
                        }
                # Fallback WoL if TV stayed in standby after KEY_POWER
                if self.mac:
                    self._send_wol(repeats=2)
                    time.sleep(1.5)
                    new_state = self.get_status().get("power_state", "unknown")
                    if new_state == "on":
                        return {
                            "ok": True,
                            "message": "Turned on the Samsung TV.",
                            "power_state": "on",
                        }
                return {
                    "ok": True,
                    "message": "Sent power-on command to the Samsung TV.",
                    "power_state": "waking",
                }
            # If WSS failed in standby, fall through to WoL

        # Deep sleep / unreachable: send Wake-on-LAN across subnet broadcast + unicast
        if self.mac:
            self._send_wol(repeats=3)
            for _ in range(5):
                time.sleep(0.8)
                st2 = self.get_status()
                if st2.get("power_state") == "on":
                    return {
                        "ok": True,
                        "message": "Turned on the Samsung TV via Wake-on-LAN.",
                        "power_state": "on",
                    }
                if st2.get("reachable") and st2.get("power_state") == "standby":
                    res = self.send_key("KEY_POWER")
                    if res.get("ok"):
                        time.sleep(1.5)
                        return {
                            "ok": True,
                            "message": "Turned on the Samsung TV.",
                            "power_state": self.get_status().get("power_state", "on"),
                        }
                    break
            return {
                "ok": False,
                "error": (
                    "Sent Wake-on-LAN, but the Samsung TV did not respond from deep sleep. "
                    "Make sure 'Power On with Mobile' is enabled in TV Network Expert Settings."
                ),
                "power_state": "off",
            }

        return self.send_key("KEY_POWER")

    def power(self, action: str) -> dict[str, Any]:
        act = (action or "").strip().lower()
        if act not in ("on", "off", "toggle"):
            return {"ok": False, "error": f"Invalid power action: {action}"}

        if act == "on":
            return self._wake_tv()

        st = self.get_status()
        cur = st.get("power_state", "unknown")

        if act == "off":
            if cur in ("standby", "off"):
                return {"ok": True, "message": "The Samsung TV is already off.", "power_state": cur}
            res = self.send_key("KEY_POWER")
            if res.get("ok"):
                res["message"] = "Turned off the Samsung TV."
                res["power_state"] = "standby"
            return res

        return self.send_key("KEY_POWER")

    def send_key(self, key: str, times: int = 1, delay: float = 0.16) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "error": "Samsung TV IP not configured"}
        k = key.strip().upper()
        if not k.startswith("KEY_"):
            k = f"KEY_{k}"
        count = max(1, min(15, int(times)))

        with self._lock:
            tv = None
            try:
                tv = self._connect(timeout=5)
                for idx in range(count):
                    tv.send_key(k)
                    if idx + 1 < count:
                        time.sleep(delay)
                return {"ok": True, "key": k, "count": count, "message": f"Sent {k} ({count}x) to TV."}
            except Exception as exc:
                logger.warning("Samsung TV send_key(%s) failed: %s", k, exc)
                return {"ok": False, "error": f"TV command failed: {exc}"}
            finally:
                if tv is not None:
                    try:
                        tv.close()
                    except Exception:
                        pass

    def volume(self, action: str, steps: int = 3) -> dict[str, Any]:
        act = (action or "").strip().lower()
        if act == "mute":
            res = self.send_key("KEY_MUTE", times=1)
            if res.get("ok"):
                res["message"] = "Toggled mute on the Samsung TV."
            return res
        if act == "up":
            n = max(1, min(15, int(steps)))
            res = self.send_key("KEY_VOLUP", times=n)
            if res.get("ok"):
                res["message"] = f"Increased TV volume by {n} steps."
            return res
        if act == "down":
            n = max(1, min(15, int(steps)))
            res = self.send_key("KEY_VOLDOWN", times=n)
            if res.get("ok"):
                res["message"] = f"Decreased TV volume by {n} steps."
            return res
        return {"ok": False, "error": f"Unknown volume action: {action}"}

    def control(self, action: str) -> dict[str, Any]:
        act = (action or "").strip().lower().replace(" ", "_").replace("-", "_")
        key = ACTION_KEYS.get(act)
        if not key:
            if act.startswith("key_"):
                key = act.upper()
            else:
                return {"ok": False, "error": f"Unsupported TV action: {action}"}
        res = self.send_key(key, times=1)
        if res.get("ok"):
            res["message"] = f"Executed '{act}' on the Samsung TV."
        return res

    def open_app(self, app_name: str) -> dict[str, Any]:
        q = (app_name or "").strip().lower()
        if not q:
            return {"ok": False, "error": "App name is required"}

        app_id = None
        display_name = app_name
        if q in KNOWN_APPS:
            app_id, display_name = KNOWN_APPS[q]
        else:
            for alias, (aid, dname) in KNOWN_APPS.items():
                if alias in q or q in alias:
                    app_id, display_name = aid, dname
                    break

        if not app_id:
            if q.isdigit() or "." in q:
                app_id = q
            else:
                return {
                    "ok": False,
                    "error": f"Unknown app '{app_name}'. Known apps: YouTube, Netflix, Spotify, Prime Video, Disney+, Apple TV, Plex, Browser.",
                }

        # Wake TV if currently in standby or deep sleep
        st = self.get_status()
        if st.get("power_state") in ("standby", "off"):
            wake_res = self._wake_tv()
            if wake_res.get("ok"):
                time.sleep(2.0)

        # Try REST launch first (fast and reliable on 2022 Tizen), fallback to WSS run_app
        try:
            r = httpx.post(f"http://{self.host}:8001/api/v2/applications/{app_id}", timeout=4.0)
            if r.status_code in (200, 201, 204):
                return {"ok": True, "app": display_name, "app_id": app_id, "message": f"Opened {display_name} on the Samsung TV."}
        except Exception as exc:
            logger.debug("REST app launch fallback to WSS: %s", exc)

        with self._lock:
            tv = None
            try:
                tv = self._connect(timeout=6)
                tv.run_app(app_id)
                return {"ok": True, "app": display_name, "app_id": app_id, "message": f"Opened {display_name} on the Samsung TV."}
            except Exception as exc:
                logger.warning("Samsung TV open_app(%s) failed: %s", app_name, exc)
                return {"ok": False, "error": f"Failed to open {display_name}: {exc}"}
            finally:
                if tv is not None:
                    try:
                        tv.close()
                    except Exception:
                        pass

    def open_url(self, url: str) -> dict[str, Any]:
        u = (url or "").strip()
        if not u:
            return {"ok": False, "error": "URL is required"}
        if not u.startswith(("http://", "https://")):
            u = "https://" + u

        # Wake TV if currently in standby or deep sleep
        st = self.get_status()
        if st.get("power_state") in ("standby", "off"):
            wake_res = self._wake_tv()
            if wake_res.get("ok"):
                time.sleep(2.0)

        with self._lock:
            tv = None
            try:
                tv = self._connect(timeout=6)
                tv.open_browser(u)
                return {"ok": True, "url": u, "message": f"Opened {u} in the Samsung TV browser."}
            except Exception as exc:
                logger.warning("Samsung TV open_url(%s) failed: %s", u, exc)
                return {"ok": False, "error": f"Failed to open URL: {exc}"}
            finally:
                if tv is not None:
                    try:
                        tv.close()
                    except Exception:
                        pass
