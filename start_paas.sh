#!/usr/bin/env bash
cd "$HOME/MikeySwarm/telemetry" || exit 1

# Kill any existing instances first
pkill -f "paas_server.py" 2>/dev/null
pkill -f "uvicorn" 2>/dev/null

# Launch via virtual environment python binary detached with nohup
nohup .venv/bin/python3 paas_server.py > /tmp/paas.log 2>&1 &

echo "[PaaS] Server running via uv environment on http://127.0.0.1:8922"
echo "[PaaS] Tail logs anytime with: cat /tmp/paas.log"
