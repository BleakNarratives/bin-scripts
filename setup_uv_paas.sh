#!/usr/bin/env bash
set -e

echo "[PaaS] Installing uv..."
curl -LsSf https://astral.sh/uv/install.sh | sh

# Export path for current subshell
export PATH="$HOME/.cargo/bin:$PATH"

echo "[PaaS] Setting up isolated environment in ~/MikeySwarm/telemetry..."
cd "$HOME/MikeySwarm/telemetry"

# Create venv and install dependencies isolated from system python
uv venv .venv
uv pip install fastapi uvicorn pydantic

echo "[PaaS] Environment initialized cleanly via uv!"
