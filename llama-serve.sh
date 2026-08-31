#!/usr/bin/env bash
# Start local llama-server for Kilo Code (OpenAI-compatible endpoint on :8890)
# Usage: ~/bin/llama-serve.sh {start|stop|status}
set -euo pipefail

PORT=8890
MODEL="$HOME/models/glue/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"
CTX=8192
THREADS=4
PIDFILE="/tmp/llama-server.pid"
LOG="/tmp/llama-server.log"
export LD_LIBRARY_PATH="$HOME/.local/llama/llama-b10662:${LD_LIBRARY_PATH:-}"

case "${1:-start}" in
  start)
    if curl -s "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ok"'; then
      echo "llama-server already running on :$PORT"
      exit 0
    fi
    export LD_LIBRARY_PATH="$HOME/.local/llama/llama-b10662:${LD_LIBRARY_PATH:-}"
    nohup "$HOME/llama.cpp/llama-server" \
      --model "$MODEL" \
      --port "$PORT" \
      --host 127.0.0.1 \
      --ctx-size "$CTX" \
      --threads "$THREADS" \
      > "$LOG" 2>&1 &
    disown
    echo $! > "$PIDFILE"
    echo "launched pid $(cat "$PIDFILE"), waiting for model load..."
    for i in $(seq 1 30); do
      if curl -s "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ok"'; then
        echo "llama-server ready on http://127.0.0.1:$PORT/v1"
        exit 0
      fi
      sleep 1
    done
    echo "ERROR: llama-server didn't become ready in 30s — check $LOG"
    exit 1
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"
    fi
    pkill -f "llama-server.*$PORT" 2>/dev/null || true
    echo "stopped"
    ;;
  status)
    if curl -s "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ok"'; then
      echo "RUNNING on :$PORT"
      curl -s "http://127.0.0.1:$PORT/v1/models" 2>/dev/null | python3 -c "import sys,json; [print(f'  model: {m[\"id\"]}') for m in json.load(sys.stdin).get('data',[])]" 2>/dev/null
    else
      echo "STOPPED"
    fi
    ;;
  *)
    echo "Usage: $0 {start|stop|status}"
    exit 1
    ;;
esac
