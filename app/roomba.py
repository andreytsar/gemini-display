from __future__ import annotations

import json
import logging
import socket
import ssl
import struct
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

PHASE_LABELS: dict[str, str] = {
    "charge": "charging on dock",
    "run": "cleaning",
    "hmUsrDock": "returning to dock",
    "hmMidMsn": "returning to dock to recharge",
    "hmPostMsn": "finished cleaning and returning to dock",
    "stop": "paused / stopped",
    "stuck": "stuck",
    "evac": "emptying bin",
    "chargeRecondition": "reconditioning battery",
    "cancelled": "cancelled",
}

ERROR_CODES: dict[int, str] = {
    0: "none",
    1: "left wheel off floor / stuck",
    2: "main brushes stuck",
    5: "left/right wheel stuck",
    6: "cliff sensor triggered",
    7: "wheel issue",
    8: "bin / suction issue",
    9: "bumper stuck",
    10: "right wheel stuck",
    11: "bin error",
    14: "reinstall dustbin",
    15: "internal error (reboot required)",
    16: "moved during start",
    17: "navigation cancelled",
    18: "unable to return to dock",
    26: "vacuum stall",
}

COMMAND_ALIASES: dict[str, str] = {
    "start": "start",
    "clean": "start",
    "vacuum": "start",
    "go": "start",
    "pause": "pause",
    "resume": "resume",
    "continue": "resume",
    "stop": "stop",
    "dock": "dock",
    "home": "dock",
    "go_home": "dock",
    "return": "dock",
    "charge": "dock",
    "base": "dock",
    "find": "find",
    "locate": "find",
    "beep": "find",
    "chime": "find",
}


def _enc_str(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("!H", len(raw)) + raw


def _enc_rem_len(length: int) -> bytes:
    out = bytearray()
    while True:
        byte = length % 128
        length //= 128
        if length > 0:
            byte |= 0x80
        out.append(byte)
        if length == 0:
            break
    return bytes(out)


def _build_connect_packet(client_id: str, username: str, password: str, keepalive: int = 30) -> bytes:
    # MQTT 3.1.1 CONNECT: CleanSession=1, Username=1, Password=1 -> flags 0xC2
    vh = b"\x00\x04MQTT\x04\xc2" + struct.pack("!H", keepalive)
    pl = _enc_str(client_id) + _enc_str(username) + _enc_str(password)
    return b"\x10" + _enc_rem_len(len(vh) + len(pl)) + vh + pl


def _build_subscribe_packet(packet_id: int, topic_filter: str) -> bytes:
    body = struct.pack("!H", packet_id) + _enc_str(topic_filter) + b"\x00"
    return b"\x82" + _enc_rem_len(len(body)) + body


def _build_publish_packet(topic: str, payload: dict[str, Any]) -> bytes:
    body = _enc_str(topic) + json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return b"\x30" + _enc_rem_len(len(body)) + body


def _parse_mqtt_packets(buf: bytearray) -> list[tuple[int, bytes]]:
    packets: list[tuple[int, bytes]] = []
    idx = 0
    total = len(buf)
    while idx < total:
        start = idx
        ptype = buf[idx] >> 4
        idx += 1
        mult = 1
        rlen = 0
        complete_len = False
        while idx < total:
            b = buf[idx]
            idx += 1
            rlen += (b & 0x7F) * mult
            mult *= 128
            if not (b & 0x80):
                complete_len = True
                break
        if not complete_len or idx + rlen > total:
            idx = start
            break
        packets.append((ptype, bytes(buf[idx : idx + rlen])))
        idx += rlen
    if idx > 0:
        del buf[:idx]
    return packets


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


class RoombaController:
    """Local MQTT-over-TLS controller for iRobot Roomba 900-series (and MQTT v3 robots)."""

    def __init__(self, host: str, blid: str, password: str, port: int = 8883) -> None:
        self.host = host.strip()
        self.blid = blid.strip()
        self.password = password.strip()
        self.port = int(port)
        self._lock = threading.Lock()
        self._last_disconnect = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.host and self.blid and self.password)

    def _make_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # Roomba 900-series firmware requires legacy renegotiation and RSA ciphers under OpenSSL 3
        ctx.set_ciphers("AES128-SHA256:AES256-SHA256:AES128-SHA:AES256-SHA:@SECLEVEL=0")
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        return ctx

    def _wait_cooldown(self) -> None:
        # Roomba allows only one local MQTT client at a time and needs ~1.5s to release a closed socket
        elapsed = time.monotonic() - self._last_disconnect
        if elapsed < 1.6:
            time.sleep(1.6 - elapsed)

    def _open_mqtt(self, timeout: float = 5.0) -> ssl.SSLSocket:
        self._wait_cooldown()
        last_exc: Exception | None = None
        for attempt in range(2):
            raw_sock = None
            ssock = None
            try:
                raw_sock = socket.create_connection((self.host, self.port), timeout=timeout)
                ssock = self._make_ssl_context().wrap_socket(raw_sock, server_hostname=self.host)
                ssock.settimeout(timeout)
                ssock.sendall(_build_connect_packet(self.blid, self.blid, self.password))
                connack = self._recv_exact(ssock, 4)
                if len(connack) != 4 or connack[0] != 0x20:
                    raise RuntimeError(f"Unexpected MQTT CONNACK: {connack.hex()}")
                if connack[3] != 0x00:
                    raise RuntimeError(f"MQTT authentication rejected (code {connack[3]})")
                return ssock
            except ConnectionRefusedError as exc:
                last_exc = exc
                if ssock is not None:
                    try:
                        ssock.close()
                    except Exception:
                        pass
                elif raw_sock is not None:
                    try:
                        raw_sock.close()
                    except Exception:
                        pass
                if attempt == 0:
                    time.sleep(2.0)
                    continue
                raise RuntimeError(
                    "Roomba refused the local MQTT connection (another client like the iRobot app may be connected)."
                ) from exc
            except Exception as exc:
                last_exc = exc
                if ssock is not None:
                    try:
                        ssock.close()
                    except Exception:
                        pass
                elif raw_sock is not None:
                    try:
                        raw_sock.close()
                    except Exception:
                        pass
                raise
        raise RuntimeError(f"Unable to connect to Roomba: {last_exc}")

    @staticmethod
    def _recv_exact(sock: ssl.SSLSocket, n: int) -> bytes:
        data = bytearray()
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)

    def _close_mqtt(self, ssock: ssl.SSLSocket | None) -> None:
        if ssock is None:
            return
        try:
            ssock.sendall(b"\xe0\x00")  # MQTT DISCONNECT
        except Exception:
            pass
        try:
            ssock.close()
        except Exception:
            pass
        self._last_disconnect = time.monotonic()

    def _collect_reported(self, ssock: ssl.SSLSocket, max_wait: float = 3.2) -> dict[str, Any]:
        ssock.sendall(_build_subscribe_packet(1, "#"))
        buf = bytearray()
        reported: dict[str, Any] = {}
        deadline = time.monotonic() + max_wait
        got_core_at: float | None = None

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if got_core_at is not None:
                grace_left = 0.35 - (time.monotonic() - got_core_at)
                if grace_left <= 0:
                    break
                remaining = min(remaining, grace_left)
            ssock.settimeout(max(0.15, remaining))
            try:
                chunk = ssock.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
            except socket.timeout:
                if got_core_at is not None:
                    break
                continue

            for ptype, body in _parse_mqtt_packets(buf):
                if ptype == 3 and len(body) >= 2:
                    tlen = struct.unpack("!H", body[:2])[0]
                    payload_bytes = body[2 + tlen :]
                    try:
                        msg = json.loads(payload_bytes.decode("utf-8", errors="replace"))
                    except Exception:
                        continue
                    rep = msg.get("state", {}).get("reported")
                    if isinstance(rep, dict):
                        _deep_merge(reported, rep)
            if got_core_at is None and "cleanMissionStatus" in reported and "batPct" in reported:
                got_core_at = time.monotonic()

        return reported

    @staticmethod
    def _format_status(reported: dict[str, Any]) -> dict[str, Any]:
        mission = reported.get("cleanMissionStatus") or {}
        bin_info = reported.get("bin") or {}
        signal = reported.get("signal") or {}

        phase = str(mission.get("phase", "unknown"))
        state = PHASE_LABELS.get(phase, phase)
        cycle = str(mission.get("cycle", "none"))
        bat_pct = reported.get("batPct")
        bin_present = bin_info.get("present")
        bin_full = bin_info.get("full")
        error_code = int(mission.get("error", 0) or 0)
        error_msg = ERROR_CODES.get(error_code, f"error code {error_code}")
        not_ready = int(mission.get("notReady", 0) or 0)
        mission_min = int(mission.get("mssnM", 0) or 0)

        carpet_boost = reported.get("carpetBoost")
        vac_high = reported.get("vacHigh")
        if carpet_boost:
            suction_mode = "auto"
        elif vac_high:
            suction_mode = "high"
        elif carpet_boost is False and vac_high is False:
            suction_mode = "eco"
        else:
            suction_mode = "unknown"

        no_auto = reported.get("noAutoPasses")
        two_pass = reported.get("twoPass")
        if no_auto is False:
            passes_mode = "auto"
        elif two_pass:
            passes_mode = "two"
        elif no_auto is True and two_pass is False:
            passes_mode = "one"
        else:
            passes_mode = "unknown"

        parts = [f"Roomba is {state}."]
        if bat_pct is not None:
            parts.append(f"Battery is at {bat_pct}%.")
        if bin_present is False:
            parts.append("Dustbin is missing.")
        elif bin_full is True:
            parts.append("Dustbin is full.")
        elif bin_full is False:
            parts.append("Dustbin is not full.")
        if error_code:
            parts.append(f"Error: {error_msg}.")
        if phase == "run" and mission_min:
            parts.append(f"Running for {mission_min} minutes.")

        return {
            "ok": True,
            "name": reported.get("name", "Roomba"),
            "sku": reported.get("sku", ""),
            "software": reported.get("softwareVer", ""),
            "phase": phase,
            "state": state,
            "cycle": cycle,
            "battery_pct": bat_pct,
            "bin_present": bin_present,
            "bin_full": bin_full,
            "error": error_code,
            "error_message": error_msg,
            "not_ready": not_ready,
            "mission_minutes": mission_min,
            "wifi_rssi": signal.get("rssi"),
            "suction_mode": suction_mode,
            "passes_mode": passes_mode,
            "bin_pause": reported.get("binPause"),
            "message": " ".join(parts),
        }

    def get_status(self) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "error": "Roomba is not configured (ROOMBA_IP / ROOMBA_BLID / ROOMBA_PASSWORD missing)."}
        with self._lock:
            ssock = None
            try:
                ssock = self._open_mqtt(timeout=5.0)
                reported = self._collect_reported(ssock, max_wait=3.2)
                if not reported:
                    return {"ok": False, "error": "Connected to Roomba, but no status telemetry was received."}
                return self._format_status(reported)
            except Exception as exc:
                logger.warning("Roomba get_status failed: %s", exc)
                return {"ok": False, "error": str(exc)}
            finally:
                self._close_mqtt(ssock)

    def command(self, action: str) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "error": "Roomba is not configured."}
        raw_act = (action or "").strip().lower().replace(" ", "_").replace("-", "_")
        act = COMMAND_ALIASES.get(raw_act)
        if not act:
            return {
                "ok": False,
                "error": f"Unsupported Roomba command '{action}'. Supported: start, pause, resume, stop, dock, find.",
            }

        with self._lock:
            ssock = None
            try:
                ssock = self._open_mqtt(timeout=5.0)
                reported = self._collect_reported(ssock, max_wait=1.5)
                status = self._format_status(reported) if reported else {}
                phase = status.get("phase", "unknown")
                cycle = status.get("cycle", "none")

                if act == "dock" and phase == "charge" and cycle == "none":
                    return {
                        "ok": True,
                        "action": "dock",
                        "already": True,
                        "battery_pct": status.get("battery_pct"),
                        "message": f"Roomba is already charging on the dock (battery {status.get('battery_pct')}%).",
                    }

                if act == "start" and phase == "run":
                    return {
                        "ok": True,
                        "action": "start",
                        "already": True,
                        "battery_pct": status.get("battery_pct"),
                        "message": f"Roomba is already cleaning (battery {status.get('battery_pct')}%).",
                    }

                if act == "resume" and phase == "charge" and cycle == "none":
                    return {
                        "ok": False,
                        "action": "resume",
                        "error": "Roomba is on the dock with no paused mission to resume. Use 'start' to begin cleaning.",
                    }

                # On Roomba 900-series, pausing briefly before docking from active 'run' is more reliable
                if act == "dock" and phase == "run":
                    ssock.sendall(
                        _build_publish_packet(
                            "cmd",
                            {"command": "pause", "time": int(time.time()), "initiator": "localApp"},
                        )
                    )
                    time.sleep(0.5)

                ssock.sendall(
                    _build_publish_packet(
                        "cmd",
                        {"command": act, "time": int(time.time()), "initiator": "localApp"},
                    )
                )
                time.sleep(0.35)

                descriptions = {
                    "start": "Started Roomba cleaning.",
                    "pause": "Paused Roomba.",
                    "resume": "Resumed Roomba cleaning.",
                    "stop": "Stopped Roomba.",
                    "dock": "Sent Roomba back to the charging dock.",
                    "find": "Triggered Roomba find chime so you can locate it.",
                }
                msg = descriptions[act]
                if status.get("battery_pct") is not None:
                    msg += f" Battery is at {status['battery_pct']}%."
                if status.get("bin_full") is True:
                    msg += " Note: the dustbin is full."

                return {
                    "ok": True,
                    "action": act,
                    "battery_pct": status.get("battery_pct"),
                    "bin_full": status.get("bin_full"),
                    "previous_phase": phase,
                    "message": msg,
                }
            except Exception as exc:
                logger.warning("Roomba command(%s) failed: %s", act, exc)
                return {"ok": False, "action": act, "error": str(exc)}
            finally:
                self._close_mqtt(ssock)

    def set_settings(
        self,
        suction: str | None = None,
        passes: str | None = None,
        bin_pause: bool | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "error": "Roomba is not configured."}

        delta: dict[str, Any] = {}
        changes: list[str] = []

        if suction is not None and suction.strip():
            s = suction.strip().lower()
            if s == "auto":
                delta["carpetBoost"] = True
                delta["vacHigh"] = False
                changes.append("suction to Auto (Carpet Boost)")
            elif s in ("high", "max", "performance"):
                delta["carpetBoost"] = False
                delta["vacHigh"] = True
                changes.append("suction to High Performance")
            elif s in ("eco", "low", "quiet"):
                delta["carpetBoost"] = False
                delta["vacHigh"] = False
                changes.append("suction to Eco")
            else:
                return {"ok": False, "error": f"Invalid suction mode '{suction}'. Use 'auto', 'high', or 'eco'."}

        if passes is not None and passes.strip():
            p = passes.strip().lower()
            if p == "auto":
                delta["noAutoPasses"] = False
                delta["twoPass"] = False
                changes.append("cleaning passes to Auto")
            elif p in ("one", "1", "single"):
                delta["noAutoPasses"] = True
                delta["twoPass"] = False
                changes.append("cleaning passes to One Pass")
            elif p in ("two", "2", "double"):
                delta["noAutoPasses"] = True
                delta["twoPass"] = True
                changes.append("cleaning passes to Two Passes")
            else:
                return {"ok": False, "error": f"Invalid passes mode '{passes}'. Use 'auto', 'one', or 'two'."}

        if bin_pause is not None:
            delta["binPause"] = bool(bin_pause)
            changes.append(f"pause-when-bin-full to {'on' if bin_pause else 'off'}")

        if not delta:
            return {"ok": False, "error": "No valid Roomba settings provided."}

        with self._lock:
            ssock = None
            try:
                ssock = self._open_mqtt(timeout=5.0)
                ssock.sendall(_build_publish_packet("delta", {"state": delta}))
                time.sleep(0.35)
                return {
                    "ok": True,
                    "updated": delta,
                    "message": "Updated Roomba " + ", ".join(changes) + ".",
                }
            except Exception as exc:
                logger.warning("Roomba set_settings(%s) failed: %s", delta, exc)
                return {"ok": False, "error": str(exc)}
            finally:
                self._close_mqtt(ssock)
