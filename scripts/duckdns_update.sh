[Unit]
Description=DuckDNS Update Script
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=pi
WorkingDirectory=/home/pi/KeukaSensorProd
ExecStart=/bin/bash /home/pi/KeukaSensorProd/scripts/duckdns_update.sh
