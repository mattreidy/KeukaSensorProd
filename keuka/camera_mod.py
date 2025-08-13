# camera_mod.py
# -----------------------------------------------------------------------------
# Camera worker (OpenCV MJPEG):
#  - Starts a background thread to grab frames.
#  - Exposes `camera.get_jpeg()` for routes to serve snapshots/streams.
#  - Works even when imported from multiple modules (single shared instance).
# -----------------------------------------------------------------------------

import sys
import time
import threading

# Ensure NumPy loads before cv2 on some Pi builds (harmless on others)
try:
    import numpy as _np  # noqa: F401
except Exception:
    _np = None

# Import OpenCV; degrade gracefully if unavailable
try:
    import cv2  # type: ignore
except Exception as _e:
    cv2 = None
    print(f"OpenCV not available: {_e}", file=sys.stderr)

from config import CAMERA_INDEX, FRAME_W, FRAME_H, MJPEG_JPEG_QUALITY

class Camera:
    def __init__(self, index=0, w=640, h=480):
        self.index = index
        self.w = w
        self.h = h
        self.cap = None
        self.lock = threading.Lock()
        self.frame = None
        self.running = False
        self.thread = threading.Thread(target=self._worker, daemon=True)

    def start(self):
        """Open camera and launch the frame-grabber thread."""
        if cv2 is None:
            return
        if self.running:
            return
        self.cap = cv2.VideoCapture(self.index)
        if not self.cap or not self.cap.isOpened():
            return
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.h)
        self.running = True
        self.thread.start()

    def _worker(self):
        """Continuously read frames and encode to JPEG bytes."""
        while self.running and self.cap and self.cap.isOpened():
            ok, frm = self.cap.read()
            if ok:
                ok2, jpg = None, None
                try:
                    ok2, jpg = cv2.imencode('.jpg', frm, [int(cv2.IMWRITE_JPEG_QUALITY), MJPEG_JPEG_QUALITY])
                except Exception:
                    ok2 = False
                if ok2:
                    with self.lock:
                        self.frame = jpg.tobytes()
            else:
                time.sleep(0.05)
        if self.cap:
            self.cap.release()

    def get_jpeg(self):
        """Return the most recent encoded JPEG frame (or None)."""
        with self.lock:
            return self.frame

    def stop(self):
        self.running = False

# Shared singleton used by all routes
camera = Camera(CAMERA_INDEX, FRAME_W, FRAME_H)
