# camera.py
# ---------
# Canonical camera manager for Keuka Sensor.
# - Tries OpenCV/V4L (USB webcams or legacy CSI via bcm2835-v4l2) when available.
# - Falls back to Picamera2/libcamera for the CSI ribbon camera (modern stack).
# - Can be forced via env: KS_CAMERA=picamera2|opencv|auto (default: auto).
# - Silences noisy OpenCV V4L warnings and avoids probing when /dev/videoN is absent.
# - Dynamically discovers Picamera2 from system dist-packages when running in a venv.
# - Detects when no camera is present and backs off retries to avoid log spam.
# - Exposes a singleton `camera` used by routes.
# - Safe to import when OpenCV/Picamera2 aren't installed (camera.available=False).

from __future__ import annotations

import io
import os
import sys
import time
import threading
import asyncio
import logging
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------- Configuration ----------------

# Preferred backend: "auto" | "opencv" | "picamera2"
CAM_BACKEND = os.environ.get("KS_CAMERA", os.environ.get("CAM_BACKEND", "auto")).strip().lower()

CAMERA_INDEX = int(os.environ.get("CAMERA_INDEX", "0"))
FRAME_W = int(os.environ.get("CAM_FRAME_W", "1280"))
FRAME_H = int(os.environ.get("CAM_FRAME_H", "720"))
JPEG_QUALITY = int(os.environ.get("CAM_JPEG_QUALITY", "85"))
FRAME_INTERVAL = float(os.environ.get("CAM_FRAME_INTERVAL", "0.05"))  # seconds between captures (20fps)
ROTATE_DEG = int(os.environ.get("CAM_ROTATE", "0"))  # 0, 90, 180, 270
BUFFER_SIZE = int(os.environ.get("CAM_BUFFER_SIZE", "5"))  # ring buffer size

# Set OpenCV log env vars BEFORE importing cv2 to reduce noise.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")  # SILENT/ERROR/WARN/INFO
os.environ.setdefault("CV_LOG_LEVEL", "ERROR")      # newer OpenCV uses this

# Optional OpenCV
try:
    import cv2  # exposed for routes_webcam.py (may be None)
    try:
        # Extra belt-and-suspenders; not present on some builds.
        if hasattr(cv2, "utils") and hasattr(cv2.utils, "logging"):
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:
        pass
except Exception:
    cv2 = None  # type: ignore

# Optional Picamera2 (first pass)
try:
    from picamera2 import Picamera2
    from libcamera import Transform
    _PICAMERA2_AVAILABLE = True
except Exception:
    Picamera2 = None  # type: ignore
    Transform = None  # type: ignore
    _PICAMERA2_AVAILABLE = False


def _ensure_picamera2_available() -> bool:
    """
    Ensure Picamera2 can be imported, even when running in a venv that
    doesn't see system dist-packages. Tries common Raspberry Pi locations.
    """
    global Picamera2, Transform, _PICAMERA2_AVAILABLE

    if _PICAMERA2_AVAILABLE:
        return True

    # Typical paths for Debian/Raspberry Pi OS
    candidate_paths = [
        "/usr/lib/python3/dist-packages",
        "/usr/local/lib/python3/dist-packages",
        # 32-bit/armhf fallback (older images)
        "/usr/lib/arm-linux-gnueabihf/python3/dist-packages",
    ]

    added = False
    for p in candidate_paths:
        if os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)
            added = True

    if not added:
        # Nothing to add; try import anyway in case the venv was created with --system-site-packages
        pass

    try:
        from picamera2 import Picamera2 as _P2  # type: ignore
        from libcamera import Transform as _T  # type: ignore
        Picamera2 = _P2  # type: ignore
        Transform = _T  # type: ignore
        _PICAMERA2_AVAILABLE = True
    except Exception:
        _PICAMERA2_AVAILABLE = False

    return _PICAMERA2_AVAILABLE


def _dev_video_exists(idx: int) -> bool:
    return os.path.exists(f"/dev/video{idx}")


class Camera:
    def __init__(self, index: int, w: int, h: int):
        self.index = index
        self.w = w
        self.h = h
        self.running = False
        self.available = False
        self.frame: Optional[bytes] = None
        self.lock = threading.Lock()
        
        # Ring buffer for async frame access
        self._frame_buffer: deque = deque(maxlen=BUFFER_SIZE)
        self._buffer_lock = threading.Lock()
        self._last_frame_time = 0.0

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

    # Public shim so routes can call camera.start()
    def start(self) -> None:
        self._start_async()

    def _try_start_opencv(self) -> bool:
        if cv2 is None:
            return False

        # If there is no V4L device node, don't even try (avoids noisy OpenCV errors).
        if not _dev_video_exists(self.index):
            return False

        try:
            # Prefer V4L2 backend where present
            cap = cv2.VideoCapture(self.index, cv2.CAP_V4L2)
            if not cap or not cap.isOpened():
                # Try default backend as a fallback
                if cap:
                    try:
                        cap.release()
                    except Exception:
                        pass
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

        # Configure resolution (best-effort)
        try:
            cap.set(3, self.w)  # cv2.CAP_PROP_FRAME_WIDTH
            cap.set(4, self.h)  # cv2.CAP_PROP_FRAME_HEIGHT
        except Exception:
            pass

        self.cap = cap
        self._mode = "opencv"
        return True

    def _try_start_picamera2(self) -> bool:
        if not _ensure_picamera2_available():
            return False
        try:
            pcam = Picamera2()  # type: ignore
            # Rotation
            rotate = ROTATE_DEG if ROTATE_DEG in (0, 90, 180, 270) else 0
            tform = Transform(rotation=rotate) if rotate else Transform()  # type: ignore

            # Video configuration for continuous capture; explicit RGB888 so we can JPEG-encode deterministically
            config = pcam.create_video_configuration(
                main={"size": (self.w, self.h), "format": "RGB888"},
                transform=tform,
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
        prefer = CAM_BACKEND
        while self.running:
            if self._mode == "none":
                started = False
                # Selection order
                if prefer == "opencv":
                    started = self._try_start_opencv() or self._try_start_picamera2()
                elif prefer == "picamera2":
                    started = self._try_start_picamera2() or self._try_start_opencv()
                else:  # "auto"
                    # If no /dev/videoN, don't bother trying OpenCV first.
                    if _dev_video_exists(self.index):
                        started = self._try_start_opencv() or self._try_start_picamera2()
                    else:
                        started = self._try_start_picamera2() or self._try_start_opencv()

                if started:
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
                    # Encode JPEG with OpenCV
                    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]) if cv2 else (False, None)
                    if not ok or buf is None:
                        raise RuntimeError("OpenCV JPEG encode failed")
                    data = bytes(buf.tobytes())

                elif self._mode == "picamera2":
                    # Capture RGB frame as numpy array
                    arr = self.pcam.capture_array("main") if self.pcam is not None else None
                    if arr is None:
                        raise RuntimeError("Picamera2 capture failed")
                    # Prefer OpenCV for speed if available; else Pillow
                    if cv2 is not None:
                        ok, buf = cv2.imencode(".jpg", arr, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
                        if not ok or buf is None:
                            raise RuntimeError("cv2.imencode failed on Picamera2 frame")
                        data = bytes(buf.tobytes())
                    else:
                        try:
                            from PIL import Image
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

                # Success: store latest frame bytes in both single frame and ring buffer
                with self.lock:
                    self.frame = data
                
                # Add to ring buffer with timestamp for async access
                with self._buffer_lock:
                    self._frame_buffer.append((time.time(), data))
                    self._last_frame_time = time.time()

                # steady-ish frame rate
                time.sleep(FRAME_INTERVAL)

            except Exception:
                # Failure: drop backend and retry with backoff
                self._shutdown_backend()
                self.available = False
                time.sleep(0.5)

    # ---------- Public API used by routes ----------

    def get_jpeg(self) -> Optional[bytes]:
        """Get the latest frame (blocking, legacy compatibility)"""
        with self.lock:
            return self.frame
    
    def get_jpeg_async(self, max_age_seconds: float = 1.0) -> Optional[bytes]:
        """Get a recent frame from buffer (non-blocking, preferred for web routes)"""
        with self._buffer_lock:
            if not self._frame_buffer:
                return None
            
            # Get the most recent frame
            timestamp, frame_data = self._frame_buffer[-1]
            
            # Check if frame is fresh enough
            if time.time() - timestamp > max_age_seconds:
                return None
                
            return frame_data
    
    async def get_jpeg_async_await(self, max_age_seconds: float = 1.0, 
                                   timeout: float = 5.0) -> Optional[bytes]:
        """
        Asynchronously wait for a fresh frame from the buffer.
        
        Args:
            max_age_seconds: Maximum acceptable age of frame
            timeout: Maximum time to wait for a fresh frame
            
        Returns:
            Fresh frame data or None on timeout
        """
        return await asyncio.to_thread(self._wait_for_fresh_frame, max_age_seconds, timeout)
    
    def _wait_for_fresh_frame(self, max_age_seconds: float, timeout: float) -> Optional[bytes]:
        """Wait for a fresh frame (blocking helper for async operation)"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            frame = self.get_jpeg_async(max_age_seconds)
            if frame is not None:
                return frame
                
            # Brief sleep to avoid busy waiting
            time.sleep(0.01)
        
        logger.warning(f"Camera frame timeout after {timeout}s")
        return None
    
    def get_buffer_stats(self) -> dict:
        """Get buffer statistics for monitoring"""
        with self._buffer_lock:
            return {
                "buffer_size": len(self._frame_buffer),
                "max_buffer_size": BUFFER_SIZE,
                "last_frame_age": time.time() - self._last_frame_time if self._last_frame_time > 0 else float('inf'),
                "available": self.available,
                "running": self.running
            }

    def stop(self) -> None:
        self.running = False
        # Let loop exit and then clean up
        time.sleep(0.2)
        self._shutdown_backend()


# Singleton used by routes
camera = Camera(CAMERA_INDEX, FRAME_W, FRAME_H)
