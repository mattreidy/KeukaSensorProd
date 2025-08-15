# camera.py
# ---------
# Canonical camera manager for Keuka Sensor.
# - Tries OpenCV/V4L first (USB webcams or legacy CSI via bcm2835-v4l2).
# - Falls back to Picamera2/libcamera for the CSI ribbon camera when V4L isn't available.
# - Silences noisy V4L warnings.
# - Detects when no camera is present and backs off retries to avoid log spam.
# - Exposes a singleton `camera` used by routes.
# - Safe to import when OpenCV/Picamera2 aren't installed (camera.available=False).

from __future__ import annotations

import os
import sys
import time
import threading
from typing import Optional

# Configuration (match existing expectations)
CAMERA_INDEX = int(os.environ.get("CAMERA_INDEX", "0"))
FRAME_W = int(os.environ.get("CAM_FRAME_W", "1280"))
FRAME_H = int(os.environ.get("CAM_FRAME_H", "720"))
JPEG_QUALITY = int(os.environ.get("CAM_JPEG_QUALITY", "85"))
FRAME_INTERVAL = float(os.environ.get("CAM_FRAME_INTERVAL", "0.1"))  # seconds between captures

# Optional OpenCV
try:
    import cv2  # exposed for routes_webcam.py (may be None)
except Exception:
    cv2 = None  # type: ignore

# Optional Picamera2
try:
    from picamera2 import Picamera2
    from libcamera import Transform
    _PICAMERA2_AVAILABLE = True
except Exception:
    Picamera2 = None  # type: ignore
    Transform = None  # type: ignore
    _PICAMERA2_AVAILABLE = False


class Camera:
    def __init__(self, index: int, w: int, h: int):
        self.index = index
        self.w = w
        self.h = h
        self.running = False
        self.available = False
        self.frame: Optional[bytes] = None
        self.lock = threading.Lock()

        # Backoff if camera missing
        self._last_fail_time = 0.0
        self._fail_backoff = 2.0  # seconds, grows up to _fail_backoff_max
        self._fail_backoff_max = 30.0

        # Backends
        self._mode = "none"  # "opencv" or "picamera2"
        self.cap = None        # OpenCV VideoCapture if used
        self.pcam = None       # Picamera2 instance if used
        self._thread: Optional[threading.Thread] = None

        self._start_async()

    # ---------- Startup / backend selection ----------

    def _start_async(self) -> None:
        if self.running:
            return
        self.running = True
        t = threading.Thread(target=self._run, name="CameraThread", daemon=True)
        t.start()
        self._thread = t

    def _try_start_opencv(self) -> bool:
        if cv2 is None:
            return False

        # Suppress V4L warnings from OpenCV
        os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")

        try:
            cap = cv2.VideoCapture(self.index, cv2.CAP_V4L2)
        except Exception:
            # Fallback to default backend if CAP_V4L2 not present
            try:
                cap = cv2.VideoCapture(self.index)
            except Exception:
                return False

        if not cap or not cap.isOpened():
            if cap:
                try:
                    cap.release()
                except Exception:
                    pass
            return False

        # Configure resolution
        cap.set(3, self.w)  # cv2.CAP_PROP_FRAME_WIDTH
        cap.set(4, self.h)  # cv2.CAP_PROP_FRAME_HEIGHT

        self.cap = cap
        self._mode = "opencv"
        return True

    def _try_start_picamera2(self) -> bool:
        if not _PICAMERA2_AVAILABLE:
            return False
        try:
            pcam = Picamera2()
            # Rotation can be controlled via CAM_ROTATE env if desired
            rotate = int(os.environ.get("CAM_ROTATE", "0"))
            t = Transform(rotation=rotate) if rotate in (0, 90, 180, 270) else Transform()

            # Video configuration for continuous capture
            config = pcam.create_video_configuration(
                main={"size": (self.w, self.h), "format": "RGB888"},
                transform=t,
                buffer_count=4,
            )
            pcam.configure(config)
            pcam.start()
            self.pcam = pcam
            self._mode = "picamera2"
            return True
        except Exception:
            # Clean up on failure
            try:
                if self.pcam:
                    self.pcam.stop()
            except Exception:
                pass
            self.pcam = None
            return False

    def _shutdown_backend(self) -> None:
        if self._mode == "opencv" and self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
        elif self._mode == "picamera2" and self.pcam is not None:
            try:
                self.pcam.stop()
            except Exception:
                pass
            self.pcam = None
        self._mode = "none"

    # ---------- Capture loop ----------

    def _run(self) -> None:
        while self.running:
            if self._mode == "none":
                # Try OpenCV/V4L first
                if self._try_start_opencv():
                    self.available = True
                # Else try Picamera2
                elif self._try_start_picamera2():
                    self.available = True
                else:
                    # Nothing available; back off
                    self.available = False
                    now = time.time()
                    if now - self._last_fail_time < self._fail_backoff:
                        time.sleep(0.2)
                        continue
                    self._last_fail_time = now
                    self._fail_backoff = min(self._fail_backoff * 2.0, self._fail_backoff_max)
                    time.sleep(self._fail_backoff)
                    continue
            try:
                if self._mode == "opencv":
                    ok, frame = self.cap.read() if self.cap is not None else (False, None)
                    if not ok or frame is None:
                        raise RuntimeError("OpenCV read failed")
                    # Encode JPEG
                    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]) if cv2 else (False, None)
                    if not ok or buf is None:
                        raise RuntimeError("OpenCV JPEG encode failed")
                    data = bytes(buf.tobytes())
                elif self._mode == "picamera2":
                    # Capture RGB frame as numpy array
                    arr = self.pcam.capture_array() if self.pcam is not None else None
                    if arr is None:
                        raise RuntimeError("Picamera2 capture failed")
                    # Use OpenCV for JPEG encoding if available; otherwise, use Pillow
                    if cv2 is not None:
                        ok, buf = cv2.imencode(".jpg", arr, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
                        if not ok or buf is None:
                            raise RuntimeError("cv2.imencode failed on Picamera2 frame")
                        data = bytes(buf.tobytes())
                    else:
                        try:
                            from PIL import Image
                            import io
                            im = Image.fromarray(arr)
                            b = io.BytesIO()
                            im.save(b, format="JPEG", quality=JPEG_QUALITY, optimize=True)
                            data = b.getvalue()
                        except Exception as e:
                            raise RuntimeError(f"Pillow JPEG encode failed: {e}")
                else:
                    # No backend active; reset and try again
                    self._shutdown_backend()
                    self.available = False
                    time.sleep(0.5)
                    continue

                # Success: store latest frame bytes
                with self.lock:
                    self.frame = data

                # steady-ish frame rate
                time.sleep(FRAME_INTERVAL)

            except Exception:
                # Failure: drop backend and retry with backoff
                self._shutdown_backend()
                self.available = False
                time.sleep(0.5)

    # ---------- Public API used by routes ----------

    def get_jpeg(self) -> Optional[bytes]:
        with self.lock:
            return self.frame

    def stop(self) -> None:
        self.running = False
        # Let loop exit and then clean up
        time.sleep(0.2)
        self._shutdown_backend()


# Singleton used by routes
camera = Camera(CAMERA_INDEX, FRAME_W, FRAME_H)
