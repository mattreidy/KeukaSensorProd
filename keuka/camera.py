# camera.py
# ---------
# Canonical camera manager for Keuka Sensor.
# - Silences noisy V4L warnings.
# - Detects when no camera is present and backs off retries to avoid log spam.
# - Exposes a singleton `camera` used by routes.
# - Safe to import when OpenCV isn't installed (camera.available=False).

from __future__ import annotations

import os
import sys
import time
import threading
from typing import Optional

try:
    import cv2  # type: ignore
    try:
        # Silence OpenCV logging (avoids repeated V4L warnings in logs)
        from cv2 import utils as cv2_utils  # type: ignore
        cv2_utils.logging.setLogLevel(cv2_utils.logging.LOG_LEVEL_SILENT)
    except Exception:
        pass
except Exception as _e:
    cv2 = None
    print(f"OpenCV not available: {_e}", file=sys.stderr)

CAMERA_INDEX = int(os.environ.get("KS_CAMERA_INDEX", "0"))
FRAME_W = int(os.environ.get("KS_FRAME_W", "640"))
FRAME_H = int(os.environ.get("KS_FRAME_H", "480"))
MJPEG_JPEG_QUALITY = int(os.environ.get("KS_JPEG_Q", "70"))

class Camera:
    def __init__(self, index: int = 0, w: int = 640, h: int = 480):
        self.index = index
        self.w = w
        self.h = h
        self.cap = None
        self.lock = threading.Lock()
        self.frame: Optional[bytes] = None
        self.running = False
        self.available = False           # True after a successful open
        self._ever_tried = False
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._next_retry_ts = 0.0        # backoff to avoid hammering missing device

    def _device_present(self) -> bool:
        dev = f"/dev/video{self.index}"
        return os.path.exists(dev) if os.name == "posix" else True

    def start(self) -> None:
        """Try to open once; uses a 60s backoff after failures."""
        if cv2 is None:
            self.available = False
            return
        now = time.time()
        if self.running or (self._ever_tried and not self.available and now < self._next_retry_ts):
            return
        self._ever_tried = True
        if not self._device_present():
            self.available = False
            self._next_retry_ts = now + 60.0
            return
        cap = cv2.VideoCapture(self.index)
        if not cap or not cap.isOpened():
            self.available = False
            self._next_retry_ts = now + 60.0
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.h)
        self.cap = cap
        self.available = True
        self.running = True
        if not self._thread.is_alive():
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()

    def _worker(self) -> None:
        while self.running and self.cap and self.cap.isOpened():
            ok, frm = self.cap.read()
            if ok:
                ok2, jpg = cv2.imencode('.jpg', frm, [int(cv2.IMWRITE_JPEG_QUALITY), MJPEG_JPEG_QUALITY])
                if ok2:
                    with self.lock:
                        self.frame = jpg.tobytes()
            else:
                time.sleep(0.05)
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass

    def get_jpeg(self) -> Optional[bytes]:
        with self.lock:
            return self.frame

    def stop(self) -> None:
        self.running = False

# Singleton used by routes
camera = Camera(CAMERA_INDEX, FRAME_W, FRAME_H)
