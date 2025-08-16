# gunicorn.conf.py
# -----------------
# Gunicorn config tuned for Raspberry Pi:
# - gthread worker for lightweight concurrency and long-lived SSE.
# - modest workers/threads to fit in limited RAM.
# - higher timeout so SSE and slow operations aren't killed.
# - max_requests guards against slow memory creep.
# - /dev/shm temp dir reduces SD card writes.

import multiprocessing
import os

bind = "0.0.0.0:5000"

# For a Pi, 2 workers with 4 threads is a good start.
workers = int(os.environ.get("KS_GUNICORN_WORKERS", "1"))
threads = int(os.environ.get("KS_GUNICORN_THREADS", "4"))
worker_class = "gthread"

# Timeouts: SSE streams push data every few seconds; give headroom.
timeout = int(os.environ.get("KS_GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.environ.get("KS_GUNICORN_GRACEFUL", "30"))
keepalive = int(os.environ.get("KS_GUNICORN_KEEPALIVE", "65"))

# Memory hygiene on small devices
max_requests = int(os.environ.get("KS_GUNICORN_MAXREQ", "1000"))
max_requests_jitter = int(os.environ.get("KS_GUNICORN_MAXJIT", "100"))
worker_tmp_dir = "/dev/shm"

# Logging to journal via systemd unit; keep simple here
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("KS_GUNICORN_LOGLEVEL", "info")
