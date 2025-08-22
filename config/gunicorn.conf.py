# Gunicorn config for Keuka Sensor on a Pi
bind = "0.0.0.0:5000"
workers = 2              # small Pi: 2 worker procs is fine
threads = 2              # each worker handles 2 threads
worker_class = "gthread" # lightweight, good for SSE
timeout = 60
graceful_timeout = 30
keepalive = 75

# Logs into journald via systemd, but leave access log on stdout if you like
errorlog = "-"
accesslog = "-"
capture_output = True

# Lower backlog to something reasonable on a Pi
backlog = 64
