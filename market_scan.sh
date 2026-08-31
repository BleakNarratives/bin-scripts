#!/usr/bin/env bash
# market_scan.sh — Run market intelligence scanner and persist signals
# Called by market-scanner.timer every hour
# Output: ~/MikeySwarm/telemetry/signals.jsonl (append-only)
# Logs: stdout goes to systemd journal

set -euo pipefail

SCANNER_DIR="$HOME/MikeySwarm/telemetry"
VENV="$SCANNER_DIR/.venv/bin/python3"
SCANNER="$SCANNER_DIR/multi_source_harvester.py"
SIGNALS_FILE="$SCANNER_DIR/signals.jsonl"
LOCK_FILE="/tmp/market_scan.lock"

# Prevent concurrent runs
if [ -f "$LOCK_FILE" ]; then
    lock_pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
        echo "[market_scan] Another scan is running (pid=$lock_pid), skipping."
        exit 0
    fi
    # Stale lock
    rm -f "$LOCK_FILE"
fi

echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

echo "[market_scan] Starting scan at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Run scanner: HN tiers + Lobsters, Reddit on full cycle every 6 hours
# Reddit flag: --reddit cycles all 5 subs with 60s delays (~5 min)
# Detect full cycle hour (every 6h: 02, 08, 14, 20 UTC)
HOUR=$(date -u +%H)
if [ $((10#$HOUR % 6)) -eq 2 ]; then
    REDDIT_FLAG="--reddit"
    REDDIT_NOTE=" [full Reddit cycle]"
else
    REDDIT_FLAG="--no-reddit"
    REDDIT_NOTE=""
fi
cd "$SCANNER_DIR"
"$VENV" "$SCANNER" \
    $REDDIT_FLAG \
    --min-score 0.15 \
    --persist \
    --json 2>&1 | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    total = data.get('total_signals', 0)
    high = len([s for s in data.get('signals', []) if s.get('composite_score', 0) >= 0.4])
    viral = len([s for s in data.get('signals', []) if s.get('tier') == 'viral'])
    elevated = len([s for s in data.get('signals', []) if s.get('tier') == 'elevated'])
    sources = list(data.get('source_summary', {}).keys())
    tiers = f'viral={viral} elevated={elevated}' if viral or elevated else 'no tier data'
    print(f'[market_scan] Complete: {total} signals ({high} high-value) from {len(sources)} sources [{tiers}]')
except Exception as e:
    print(f'[market_scan] Parse error: {e}')
"
echo "[market_scan] Reddit mode: ${REDDIT_FLAG}${REDDIT_NOTE}"

# Show accumulated totals
if [ -f "$SIGNALS_FILE" ]; then
    total_lines=$(wc -l < "$SIGNALS_FILE")
    echo "[market_scan] Total signals in ledger: $total_lines"
fi

echo "[market_scan] Done at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
