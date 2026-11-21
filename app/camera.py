from __future__ import annotations

import io
import logging
import threading
import time
from typing import Callable

from PIL import Image

logger = logging.getLogger(__name__)


class CameraManager:
    """On-demand CSI camera manager using Picamera2.

    The camera only runs while at least one consumer holds a lease via acquire(),
    ensuring zero idle CPU usage and hardware privacy when not in video mode.
    """

    def __init__(
        self,
        get_rotation: Callable[[], int],
        get_hflip: Callable[[], bool] = lambda: True,
        fps: float = 8.0,
        jpeg_quality: int = 68,
    ) -> None:
        self._get_rotation = get_rotation
        self._get_hflip = get_hflip
        self._fps = fps
        self._jpeg_quality = jpeg_quality

        self._lock = threading.Lock()
        self._frame_cond = threading.Condition(self._lock)
        self._refcount = 0
        self._running = False
        self._stop_timer: threading.Timer | None = None
        self._thread: threading.Thread | None = None
        self._latest_jpeg: bytes | None = None
        self._frame_seq = 0
        self._last_error: str | None = None

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._running

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def acquire(self) -> None:
        with self._lock:
            if self._stop_timer is not None:
                self._stop_timer.cancel()
                self._stop_timer = None
            self._refcount += 1
            if not self._running:
                self._running = True
                self._last_error = None
                self._thread = threading.Thread(target=self._capture_loop, name="picam-capture", daemon=True)
                self._thread.start()

    def release(self) -> None:
        with self._lock:
            self._refcount = max(0, self._refcount - 1)
            if self._refcount == 0 and self._running:
                if self._stop_timer is not None:
                    self._stop_timer.cancel()
                self._stop_timer = threading.Timer(1.5, self._shutdown_if_unused)
                self._stop_timer.daemon = True
                self._stop_timer.start()

    def force_stop(self) -> None:
        with self._lock:
            self._refcount = 0
            if self._stop_timer is not None:
                self._stop_timer.cancel()
                self._stop_timer = None
            self._running = False
            self._frame_cond.notify_all()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=3.0)

    def _shutdown_if_unused(self) -> None:
        with self._lock:
            if self._refcount == 0:
                self._running = False
                self._frame_cond.notify_all()

    def get_latest_jpeg(self, wait_timeout: float = 1.0) -> bytes | None:
        with self._lock:
            if self._latest_jpeg is None and self._running:
                self._frame_cond.wait(timeout=wait_timeout)
            return self._latest_jpeg

    def wait_next_jpeg(self, last_seq: int, timeout: float = 1.0) -> tuple[int, bytes | None]:
        with self._lock:
            if self._frame_seq == last_seq and self._running:
                self._frame_cond.wait(timeout=timeout)
            return self._frame_seq, self._latest_jpeg

    def _capture_loop(self) -> None:
        picam2 = None
        try:
            from picamera2 import Picamera2

            picam2 = Picamera2()
            cfg = picam2.create_preview_configuration(main={"size": (512, 384)})
            picam2.configure(cfg)
            picam2.start()
            logger.info("Picamera2 started (512x384)")

            interval = 1.0 / max(1.0, self._fps)
            while True:
                with self._lock:
                    if not self._running:
                        break
                t0 = time.monotonic()
                img = picam2.capture_image("main")
                if img.mode != "RGB":
                    img = img.convert("RGB")
                rot = self._get_rotation()
                if rot == 180:
                    img = img.transpose(Image.Transpose.ROTATE_180)
                elif rot == 90:
                    img = img.transpose(Image.Transpose.ROTATE_90)
                elif rot == 270:
                    img = img.transpose(Image.Transpose.ROTATE_270)
                if self._get_hflip():
                    img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=self._jpeg_quality, optimize=False)
                jpeg = buf.getvalue()

                with self._lock:
                    self._latest_jpeg = jpeg
                    self._frame_seq += 1
                    self._frame_cond.notify_all()

                elapsed = time.monotonic() - t0
                if elapsed < interval:
                    time.sleep(interval - elapsed)
        except Exception as exc:
            logger.exception("Camera capture error: %s", exc)
            with self._lock:
                self._last_error = str(exc)
                self._running = False
                self._frame_cond.notify_all()
        finally:
            if picam2 is not None:
                try:
                    picam2.stop()
                except Exception:
                    pass
                try:
                    picam2.close()
                except Exception:
                    pass
                logger.info("Picamera2 stopped and closed")
            with self._lock:
                self._latest_jpeg = None
                self._running = False
                self._frame_cond.notify_all()
