#!/bin/bash
# ROUND ENGINE - Market scraping + condition triggers + hooks
# Usage: round.sh [round_id] | round.sh --selftest
#
# Live BTC/USD via CoinGecko (free, no key), Binance fallback.
# FAILS CLOSED: if both APIs fail the round is logged and skipped —
# no fake prices, no false triggers.
#
# TRIGGERS (evaluated in order):
#   1. Percent move vs last round (primary):
#        |move| >= PRICE_MOVE_PCT (default 1.5%) -> high/low trigger
#   2. Fixed bands (backstop, always on):
#        price > PRICE_HIGH_TRIGGER (default 100000) -> high trigger
#        price < PRICE_LOW_TRIGGER  (default 80000)  -> low trigger
#   High -> payment hook + high_price_<id>; Low -> low_price_<id>.
#
# Also enforces the cache guard every round (see bin/cache_guard.sh).

ROUND_ID=${1:-$(date +%Y%m%d%H%M)}
TRIGGER_DIR=~/triggers
LOG=~/rounds.log
HIGH=${PRICE_HIGH_TRIGGER:-100000}
LOW=${PRICE_LOW_TRIGGER:-80000}
MOVE_PCT=${PRICE_MOVE_PCT:-1.5}
CACHE_LIMIT_GB=${CACHE_GUARD_LIMIT_GB:-3}
TRIGGER_KEEP=${TRIGGER_KEEP_COUNT:-50}
mkdir -p "$TRIGGER_DIR"

# Live market data — CoinGecko primary, Binance fallback
scrape() {
    local price=""
    # CoinGecko: BTC/USD (3s timeout so a hung API can't stall the round)
    price=$(curl -sf -m 3 "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd" \
        | jq -r '.bitcoin.usd // empty' 2>/dev/null)
    # Binance fallback: last trade price
    if [ -z "$price" ]; then
        price=$(curl -sf -m 3 "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT" \
            | jq -r '.price // empty' 2>/dev/null)
    fi
    # Validate: positive number
    if ! echo "$price" | grep -qE '^[0-9]+(\.[0-9]+)?$' || [ "${price%%.*}" -le 0 ]; then
        echo "$(date -Iseconds), $ROUND_ID, ERROR: all price APIs failed — round skipped" >> $LOG
        return 1
    fi
    echo "$(date -Iseconds), $ROUND_ID, btc_usd=$price" >> $LOG
    # Write latest full-precision price for hooks to read
    echo "$price" > ~/current_price.txt
    echo "$price"
}

# Evaluate conditions. Args: price last_price
evaluate() {
    local PRICE=$1 LAST=$2
    local PRICE_INT=${PRICE%%.*}
    local move="" verdict=""

    if [ -n "$LAST" ] && echo "$LAST" | grep -qE '^[0-9]+(\.[0-9]+)?$'; then
        move=$(awk -v n="$PRICE" -v l="$LAST" 'BEGIN{printf "%+.2f", (n-l)/l*100}')
    fi

    if [ -n "$move" ] && [ "$move" != "-0.00" ]; then
        local is_up is_dn
        is_up=$(awk -v m="$move" -v t="$MOVE_PCT" 'BEGIN{print (m >= t) ? 1 : 0}')
        is_dn=$(awk -v m="$move" -v t="$MOVE_PCT" 'BEGIN{print (m <= -t) ? 1 : 0}')
        if [ "$is_up" = "1" ]; then
            verdict="high_move"
        elif [ "$is_dn" = "1" ]; then
            verdict="low_move"
        fi
    fi

    # Fixed bands remain as an absolute backstop
    if [ -z "$verdict" ] && [ "$PRICE_INT" -gt "$HIGH" ]; then
        verdict="high_band"
    elif [ -z "$verdict" ] && [ "$PRICE_INT" -lt "$LOW" ]; then
        verdict="low_band"
    fi

    case "$verdict" in
        high_move)
            echo "[$verdict] Price \$${PRICE} moved ${move}% vs last round (>= ${MOVE_PCT}%) → triggering payment." >> $LOG
            ~/bin/master_control.sh charge "$PRICE_INT" "round_$ROUND_ID" >> $LOG 2>&1
            touch $TRIGGER_DIR/high_price_$ROUND_ID
            ;;
        low_move)
            echo "[$verdict] Price \$${PRICE} moved ${move}% vs last round (<= -${MOVE_PCT}%) → low alert." >> $LOG
            touch $TRIGGER_DIR/low_price_$ROUND_ID
            ;;
        high_band)
            echo "[$verdict] Price \$${PRICE_INT} > \$$HIGH band → triggering payment." >> $LOG
            ~/bin/master_control.sh charge "$PRICE_INT" "round_$ROUND_ID" >> $LOG 2>&1
            touch $TRIGGER_DIR/high_price_$ROUND_ID
            ;;
        low_band)
            echo "[$verdict] Price \$${PRICE_INT} < \$$LOW band → low alert." >> $LOG
            touch $TRIGGER_DIR/low_price_$ROUND_ID
            ;;
        *)
            if [ -n "$move" ]; then
                echo "Price \$${PRICE} (${move}% vs last) normal; band \$${LOW}-\$$HIGH ok." >> $LOG
            else
                echo "Price \$${PRICE_INT} normal (cold start, band \$${LOW}-\$$HIGH)." >> $LOG
            fi
            ;;
    esac
}

# Trigger GC — keep only the last N trigger files, delete stale + junk.
# This is bounded nominal-time maintenance; the overseer retains its own
# pressure-time cleanup path and is intentionally unchanged.
trigger_gc() {
    local count=$1
    local total junk kept deleted=0
    if ! [[ "$count" =~ ^[0-9]+$ ]] || [ "$count" -lt 1 ]; then
        echo "trigger_gc: invalid retention count: $count" >> "$LOG"
        return 2
    fi
    total=$(find "$TRIGGER_DIR" -maxdepth 1 -type f 2>/dev/null | wc -l)
    # Delete non-conforming files (e.g. high_price_--selftest)
    junk=$(find "$TRIGGER_DIR" -maxdepth 1 -type f ! -name 'high_price_*' ! -name 'low_price_*' -print 2>/dev/null)
    if [ -n "$junk" ]; then
        while IFS= read -r f; do
            rm -f -- "$f"
            deleted=$((deleted+1))
        done <<< "$junk"
    fi
    # Sort remaining by name (chronological), keep newest N, delete the rest
    kept=$(find "$TRIGGER_DIR" -maxdepth 1 -type f \( -name 'high_price_*' -o -name 'low_price_*' \) -printf '%f\n' 2>/dev/null | sort | tail -n "$count" | wc -l)
    if [ "$kept" -lt "$total" ]; then
        find "$TRIGGER_DIR" -maxdepth 1 -type f \( -name 'high_price_*' -o -name 'low_price_*' \) -printf '%f\n' 2>/dev/null | sort | head -n -"$count" | while IFS= read -r f; do
            rm -f -- "$TRIGGER_DIR/$f"
        done
    fi
    # Recount after deletion so the log reflects actual bounded retention.
    local remaining
    remaining=$(find "$TRIGGER_DIR" -maxdepth 1 -type f \( -name 'high_price_*' -o -name 'low_price_*' \) -printf '%f\n' 2>/dev/null | wc -l)
    if [ "$total" -gt 0 ]; then
        echo "[$(date -Iseconds)] trigger_gc: ${total} files -> kept ${remaining}, deleted $((total - remaining))" >> "$LOG"
    fi
}

# Main round
run_round() {
    echo "=== Round $ROUND_ID started ===" >> $LOG
    # Trigger GC: keep only the last $TRIGGER_KEEP trigger files
    # Nominal-time cleanup; pressure-time cleanup remains in the overseer.
    trigger_gc "$TRIGGER_KEEP"
    # Cache guard: purge regenerable build caches that crossed the limit
    ~/bin/cache_guard.sh "$CACHE_LIMIT_GB" >> $LOG 2>&1 || true
    # DVM heartbeat: latest revenue-rail ledger event
    DVM_LEDGER="$HOME/passive_income_swarm/nostr_dvm/logs/jobs.jsonl"
    [ -f "$DVM_LEDGER" ] && { tail -1 "$DVM_LEDGER" | python3 -c "
import sys, json, datetime
r = json.loads(sys.stdin.readline())
print(datetime.datetime.fromtimestamp(r['ts']).strftime('%m-%d %H:%M'),
      'dvm:', r['outcome'], str(r.get('amount_msat', 0)) + 'msat')" >> $LOG 2>&1 || true; }
    # Telemetry harvest: collect agent logs from Kilo/Vibe/Gemini into unified.jsonl
    # --since 15m covers the 10-min round interval with a small buffer
    HARVEST_OUT=$(cd "$HOME/MikeySwarm/telemetry" && python3 harvest.py --since 15m --json 2>&1)
    HARVEST_WRITTEN=$(echo "$HARVEST_OUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('written',0))" 2>/dev/null || echo 0)
    HARVEST_TOTAL=$(echo "$HARVEST_OUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_seen',0))" 2>/dev/null || echo 0)
    echo "[$(date -Iseconds)] harvest: +${HARVEST_WRITTEN} new, ${HARVEST_TOTAL} total -> unified.jsonl" >> "$LOG"
    LAST_PRICE=""
    [ -f ~/current_price.txt ] && LAST_PRICE=$(cat ~/current_price.txt)
    PRICE=$(scrape) || { echo "=== Round $ROUND_ID aborted (API failure) ===" >> $LOG; exit 1; }
    evaluate "$PRICE" "$LAST_PRICE"
    echo "=== Round done ===" >> $LOG
}

# Self-test: drive evaluate() through synthetic scenarios (no API calls)
selftest() {
    local pass=0 fail=0
    check() { # desc full_log_content expected_pattern
        if grep -q "$3" <<<"$2"; then echo "  PASS: $1"; pass=$((pass+1));
        else echo "  FAIL: $1 -> last: $(tail -1 <<<"$2")"; fail=$((fail+1)); fi
    }
    T=$(mktemp); LOG="$T"
    echo "-- selftest: MOVE_PCT=$MOVE_PCT bands $LOW-$HIGH"
    evaluate 85000 ""                    >/dev/null; check "cold start normal"      "$(cat $T)" "cold start"
    evaluate 81000 80467                 >/dev/null; check "+0.66% normal"          "$(cat $T)" "normal"
    evaluate 83000 81000                 >/dev/null; check "+2.47% -> high_move"    "$(cat $T)" "high_move"
    evaluate 79000 81000                 >/dev/null; check "-2.47% -> low_move"     "$(cat $T)" "low_move"
    evaluate 101000 101000               >/dev/null; check "flat but > band high"   "$(cat $T)" "high_band"
    evaluate 70000 70000                 >/dev/null; check "flat but < band low"    "$(cat $T)" "low_band"
    evaluate 99999.99 98999              >/dev/null; check "+1.01% under threshold" "$(cat $T)" "normal"
    rm -f "$T"

    # -- trigger_gc test --
    echo "-- selftest: trigger_gc"
    local TMP_TRIG
    TMP_TRIG=$(mktemp -d)
    TRIGGER_DIR="$TMP_TRIG"
    TRIGGER_KEEP=3
    # Create 10 trigger files + 1 junk file
    for i in $(seq -w 1 10); do touch "$TMP_TRIG/low_price_2026082600${i}0"; done
    touch "$TMP_TRIG/high_price_--selftest_junk"
    local before
    before=$(ls "$TMP_TRIG" | wc -l)
    trigger_gc 3
    local after junk_left
    after=$(ls "$TMP_TRIG" | wc -l)
    junk_left=$(ls "$TMP_TRIG" | grep -c 'selftest_junk' || true)
    if [ "$before" = "11" ]; then echo "  PASS: GC started with 11 files"; pass=$((pass+1)); else echo "  FAIL: GC expected 11 files, got $before"; fail=$((fail+1)); fi
    if [ "$after" = "3" ]; then echo "  PASS: GC kept 3 files"; pass=$((pass+1)); else echo "  FAIL: GC expected 3 files, got $after"; fail=$((fail+1)); fi
    if [ "$junk_left" = "0" ]; then echo "  PASS: GC removed junk file"; pass=$((pass+1)); else echo "  FAIL: junk file still present"; fail=$((fail+1)); fi
    rm -rf "$TMP_TRIG"

    echo "-- selftest: $pass passed, $fail failed"
    [ $fail -eq 0 ]
}

case "${1:-}" in
    --selftest) selftest ;;
    *) run_round ;;
esac
