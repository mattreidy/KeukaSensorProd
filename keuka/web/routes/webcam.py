# routes_webcam.py
# -----------------------------------------------------------------------------
# Webcam routes:
#   /webcam   - simple page that shows the MJPEG stream
#   /stream   - multipart/x-mixed-replace MJPEG stream (backend-agnostic)
#   /snapshot - single JPEG frame for use as a lightweight thumbnail
# -----------------------------------------------------------------------------

import time
from flask import Blueprint, Response, abort
from ...camera import camera  # backend-agnostic; produces JPEG bytes
from ...ui import render_page

webcam_bp = Blueprint("webcam", __name__)

@webcam_bp.route("/webcam")
def webcam_page():
    body = """
      <h1>Webcam</h1>
      <div class="card">
        <p class="muted">Live MJPEG stream.</p>
        <img src="/stream" alt="Webcam stream" style="max-width:100%;height:auto;border-radius:12px;border:1px solid var(--border)">
      </div>
      <p class="muted">If the image does not load, check service logs for camera initialization errors.</p>
    """
    return render_page("Keuka Sensor – Webcam", body)

@webcam_bp.route("/stream")
def stream_mjpeg():
    # Ensure background capture thread is running
    if not camera.running:
        try:
            camera.start()
        except Exception:
            pass

    # Warm-up: wait briefly for first frame
    t0 = time.time()
    while camera.get_jpeg() is None and time.time() - t0 < 3.0:
        time.sleep(0.05)

    if camera.get_jpeg() is None:
        abort(503, "No camera frames available.")

    def gen():
        boundary = b"frame"
        # Note: camera.get_jpeg() yields already-encoded JPEG bytes.
        while True:
            frm = camera.get_jpeg()
            if frm is None:
                time.sleep(0.05)
                continue
            yield (
                b"--" + boundary + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Cache-Control: no-cache\r\n\r\n" +
                frm + b"\r\n"
            )

    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

@webcam_bp.route("/snapshot")
def snapshot_jpeg():
    if not camera.running:
        try:
            camera.start()
        except Exception:
            pass

    # Wait briefly for a frame
    t0 = time.time()
    frm = None
    while time.time() - t0 < 2.0:
        frm = camera.get_jpeg()
        if frm:
            break
        time.sleep(0.05)

    if not frm:
        abort(503, "No frame")

    resp = Response(frm, mimetype="image/jpeg")
    # Cache-busting headers
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp
