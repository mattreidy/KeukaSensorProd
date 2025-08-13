# routes_webcam.py
# -----------------------------------------------------------------------------
# Webcam routes:
#   /webcam   - simple page that shows the MJPEG stream
#   /stream   - multipart/x-mixed-replace MJPEG stream (requires OpenCV)
#   /snapshot - single JPEG frame for use as a lightweight thumbnail
# -----------------------------------------------------------------------------

import time
from flask import Blueprint, Response, abort
from camera import camera, cv2  # cv2 may be None; routes handle that
from ui import render_page

webcam_bp = Blueprint("webcam", __name__)

@webcam_bp.route("/webcam")
def webcam_page():
    body = """
      <h1>Webcam</h1>
      <div class="card">
        <p class="muted">Live MJPEG stream.</p>
        <img src="/stream" alt="Webcam stream" style="max-width:100%;height:auto;border-radius:12px;border:1px solid var(--border)">
      </div>
      <p class="muted">If the image does not load, OpenCV may be unavailable.</p>
    """
    return render_page("Keuka Sensor – Webcam", body)

@webcam_bp.route("/stream")
def stream_mjpeg():
    if cv2 is None:
        abort(503, 'Webcam not available (OpenCV missing).')
    if not camera.running:
        camera.start()

    def gen():
        boundary = 'frame'
        while True:
            frm = camera.get_jpeg()
            if frm is None:
                time.sleep(0.05)
                continue
            # Each chunk is: boundary + headers + jpeg bytes + CRLF
            yield (b"--" + boundary.encode() + b"\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frm + b"\r\n")

    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

@webcam_bp.route("/snapshot")
def snapshot_jpeg():
    if cv2 is None:
        abort(503, 'Webcam not available (OpenCV missing).')
    if not camera.running:
        camera.start()

    # Wait briefly for a frame
    t0 = time.time()
    frm = None
    while time.time() - t0 < 2.0:
        frm = camera.get_jpeg()
        if frm:
            break
        time.sleep(0.05)
    if not frm:
        abort(503, 'No frame')
    resp = Response(frm, mimetype='image/jpeg')
    # Cache-busting headers (so the health page can append ?cb=time)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp
