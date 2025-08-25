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

    # Quick check for camera availability using non-blocking buffer
    initial_frame = camera.get_jpeg_async(max_age_seconds=2.0)
    if initial_frame is None and not camera.available:
        abort(503, "No camera frames available.")

    def gen():
        boundary = b"frame"
        last_frame = None
        frame_repeat_count = 0
        max_repeats = 10  # Prevent stuck frames
        
        while True:
            # Use async buffer access - non-blocking
            frm = camera.get_jpeg_async(max_age_seconds=0.5)
            
            if frm is None:
                # If no fresh frame and camera not available, break
                if not camera.available:
                    break
                # Brief sleep and try again
                time.sleep(0.02)
                continue
            
            # Prevent sending identical frames repeatedly
            if frm == last_frame:
                frame_repeat_count += 1
                if frame_repeat_count > max_repeats:
                    time.sleep(0.02)
                    continue
            else:
                frame_repeat_count = 0
                last_frame = frm
            
            yield (
                b"--" + boundary + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Cache-Control: no-cache\r\n\r\n" +
                frm + b"\r\n"
            )
            
            # Small delay to prevent overwhelming the client
            time.sleep(0.02)

    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

@webcam_bp.route("/snapshot")
def snapshot_jpeg():
    if not camera.running:
        try:
            camera.start()
        except Exception:
            pass

    # Try to get a recent frame from buffer (non-blocking)
    frm = camera.get_jpeg_async(max_age_seconds=1.0)
    
    # If no recent frame available, fall back to blocking call with short timeout
    if frm is None:
        t0 = time.time()
        while time.time() - t0 < 1.0:  # Reduced timeout
            frm = camera.get_jpeg_async(max_age_seconds=2.0)
            if frm:
                break
            time.sleep(0.02)  # Shorter sleep

    if not frm:
        abort(503, "No frame available")

    resp = Response(frm, mimetype="image/jpeg")
    # Cache-busting headers
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@webcam_bp.route("/camera/stats")
def camera_stats():
    """Get camera buffer statistics for monitoring and debugging"""
    stats = camera.get_buffer_stats()
    return {"ok": True, "stats": stats}
