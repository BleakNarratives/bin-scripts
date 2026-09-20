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
# Verdict memory, so band alerts can be edge-triggered instead of re-announced
# every round while the condition merely persists.
STATE=${ROUND_STATE_FILE:-$HOME/.round_last_verdict}
# A heartbeat whose ledger event is older than this is reported as STALE rather
# than presented as a live reading. Rounds fire every 5-10m; an hour is well past
# that, so a stale line means the rail really has stopped writing.
DVM_STALE_S=${DVM_STALE_AFTER_S:-3600}
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

# Single funnel for the payment path.
# ROUND_DRY_RUN=1 suppresses the charge (used by --selftest, which would
# otherwise POST a real charge for its synthetic high_band case).
charge_hook() {
    if [ -n "$ROUND_DRY_RUN" ]; then
        echo "[dry-run] would charge \$$1 ($2)" >> "$LOG"
        return 0
    fi
    ~/bin/master_control.sh charge "$1" "$2" >> "$LOG" 2>&1
}

# Evaluate conditions. Args: price last_price
evaluate() {
    local PRICE=$1 LAST=$2
    local PRICE_INT=${PRICE%%.*}
    local move="" verdict=""
    # Previous round's verdict, read before this round is classified.
    local prev=""
    [ -f "$STATE" ] && prev=$(cat "$STATE" 2>/dev/null)

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
            # A move-triggered charge must not fire while we are in the low
            # regime: a bounce off a sub-band floor is not a monetizable
            # breakout, and charging on it inverts the band's meaning.
            if [ "$PRICE_INT" -lt "$LOW" ]; then
                echo "[$verdict] Price \$${PRICE} moved ${move}% (>= ${MOVE_PCT}%) but is below the \$$LOW band → payment withheld." >> $LOG
            else
                echo "[$verdict] Price \$${PRICE} moved ${move}% vs last round (>= ${MOVE_PCT}%) → triggering payment." >> $LOG
                charge_hook "$PRICE_INT" "round_$ROUND_ID"
                touch $TRIGGER_DIR/high_price_$ROUND_ID
            fi
            ;;
        low_move)
            echo "[$verdict] Price \$${PRICE} moved ${move}% vs last round (<= -${MOVE_PCT}%) → low alert." >> $LOG
            touch $TRIGGER_DIR/low_price_$ROUND_ID
            ;;
        high_band)
            echo "[$verdict] Price \$${PRICE_INT} > \$$HIGH band → triggering payment." >> $LOG
            charge_hook "$PRICE_INT" "round_$ROUND_ID"
            touch $TRIGGER_DIR/high_price_$ROUND_ID
            ;;
        low_band)
            # Edge-triggered: a price below the low band is a level, not an event.
            # Announcing it every round produced 2,016 identical alerts and buried
            # everything else in the log. Alert on the transition into the band;
            # while the price merely stays low, record a quiet no-op line.
            if [ "$prev" = "low_band" ]; then
                echo "[$verdict] Price \$${PRICE_INT} < \$$LOW band → still low (no new alert)." >> $LOG
            else
                echo "[$verdict] Price \$${PRICE_INT} < \$$LOW band → low alert." >> $LOG
                touch $TRIGGER_DIR/low_price_$ROUND_ID
            fi
            ;;
        *)
            if [ -n "$move" ]; then
                echo "Price \$${PRICE} (${move}% vs last) normal; band \$${LOW}-\$$HIGH ok." >> $LOG
            else
                echo "Price \$${PRICE_INT} normal (cold start, band \$${LOW}-\$$HIGH)." >> $LOG
            fi
            ;;
    esac

    # Persist this round's verdict so the next round can detect the transition.
    printf '%s' "$verdict" > "$STATE" 2>/dev/null || true
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

# DVM heartbeat: latest revenue-rail ledger event.
#
# The ledger event's own timestamp is compared against the clock. Without that
# check a bare `tail -1` reprints whatever the last event was, forever, with
# nothing distinguishing a live rail from one that stopped writing on
# 2026-08-26 -- that single stale line was re-announced 2,176 times. A fresh
# event still prints in the original format; once it falls behind it gains an
# explicit STALE marker and its age.
dvm_heartbeat() {
    local ledger
    ledger=${DVM_LEDGER:-$HOME/passive_income_swarm/nostr_dvm/logs/jobs.jsonl}
    [ -f "$ledger" ] || return 0
    DVM_STALE_S="$DVM_STALE_S" DVM_LEDGER="$ledger" python3 -c '
import datetime, json, os, sys, time
stale_s = float(os.environ.get("DVM_STALE_S") or 3600)
try:
    with open(os.environ["DVM_LEDGER"], encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    rec = json.loads(lines[-1]) if lines else {}
except Exception as exc:
    print("dvm: ledger unreadable:", exc)
    sys.exit(0)
ts = float(rec.get("ts") or 0)
if ts <= 0:
    print("dvm: last ledger event carries no timestamp")
    sys.exit(0)
age = max(0.0, time.time() - ts)
stamp = datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
body = "dvm: %s %smsat" % (rec.get("outcome", "unknown"), rec.get("amount_msat", 0))
if age > stale_s:
    print("%s %s [STALE: last event %dm ago]" % (stamp, body, int(age // 60)))
else:
    print("%s %s" % (stamp, body))
' >> "$LOG" 2>&1 || true
}

# Main round
run_round() {
    echo "=== Round $ROUND_ID started ===" >> $LOG
    # Trigger GC: keep only the last $TRIGGER_KEEP trigger files
    # Nominal-time cleanup; pressure-time cleanup remains in the overseer.
    trigger_gc "$TRIGGER_KEEP"
    # Cache guard: purge regenerable build caches that crossed the limit
    ~/bin/cache_guard.sh "$CACHE_LIMIT_GB" >> $LOG 2>&1 || true
    dvm_heartbeat
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
    check_absent() { # desc full_log_content unexpected_pattern
        if grep -q "$3" <<<"$2"; then
            echo "  FAIL: $1 -> unexpected: $(grep "$3" <<<"$2" | tail -1)"; fail=$((fail+1))
        else echo "  PASS: $1"; pass=$((pass+1)); fi
    }
    T=$(mktemp); LOG="$T"
    STATE=$(mktemp)   # verdict memory must not persist across selftest runs
    ROUND_DRY_RUN=1   # never POST a charge from a self-test
    echo "-- selftest: MOVE_PCT=$MOVE_PCT bands $LOW-$HIGH"
    evaluate 85000 ""                    >/dev/null; check "cold start normal"      "$(cat $T)" "cold start"
    evaluate 81000 80467                 >/dev/null; check "+0.66% normal"          "$(cat $T)" "normal"
    evaluate 83000 81000                 >/dev/null; check "+2.47% -> high_move"    "$(cat $T)" "high_move"
    evaluate 79000 81000                 >/dev/null; check "-2.47% -> low_move"     "$(cat $T)" "low_move"
    evaluate 101000 101000               >/dev/null; check "flat but > band high"   "$(cat $T)" "high_band"
    evaluate 70000 70000                 >/dev/null; check "flat but < band low"    "$(cat $T)" "low_band"
    evaluate 99999.99 98999              >/dev/null; check "+1.01% under threshold" "$(cat $T)" "normal"
    evaluate 79000 77000                 >/dev/null; check "+2.6% below low band -> withheld" "$(cat $T)" "withheld"
    evaluate 83000 78000                 >/dev/null; check "+6.4% above low band -> charges"   "$(cat $T)" "triggering payment"
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

    # -- low_band must be edge-triggered, not re-announced every round --
    echo "-- selftest: low_band edge trigger"
    local TMP_LB BEFORE AFTER
    TMP_LB=$(mktemp -d)
    TRIGGER_DIR="$TMP_LB"
    T=$(mktemp); LOG="$T"; STATE="$TMP_LB/state"
    evaluate 70000 70000 >/dev/null
    check "entry into low band alerts"       "$(cat $T)" "low alert"
    check "entry writes a low trigger"       "$(ls $TMP_LB)" "low_price_"
    BEFORE=$(ls "$TMP_LB" | grep -c 'low_price_' || true)
    T=$(mktemp); LOG="$T"
    evaluate 70000 70000 >/dev/null
    check "still-low round is quiet"         "$(cat $T)" "still low"
    check_absent "still-low does not re-alert" "$(cat $T)" "low alert"
    AFTER=$(ls "$TMP_LB" | grep -c 'low_price_' || true)
    if [ "$BEFORE" = "$AFTER" ]; then echo "  PASS: no duplicate trigger file"; pass=$((pass+1));
    else echo "  FAIL: trigger files grew $BEFORE -> $AFTER"; fail=$((fail+1)); fi
    evaluate 85000 "" >/dev/null     # leave the band
    T=$(mktemp); LOG="$T"
    evaluate 70000 70000 >/dev/null  # and re-enter it
    check "re-entry after leaving alerts"    "$(cat $T)" "low alert"
    rm -rf "$TMP_LB"

    # -- dvm heartbeat must not present a dead rail as live --
    echo "-- selftest: dvm heartbeat staleness"
    local TMP_DVM
    TMP_DVM=$(mktemp -d)
    DVM_LEDGER="$TMP_DVM/jobs.jsonl"
    T=$(mktemp); LOG="$T"
    printf '{"ts": %d, "outcome": "served_free", "amount_msat": 0}\n' "$(date +%s)" > "$DVM_LEDGER"
    dvm_heartbeat
    check "fresh ledger reports the event"   "$(cat $T)" "dvm: served_free 0msat"
    check_absent "fresh ledger is not STALE"  "$(cat $T)" "STALE"
    T=$(mktemp); LOG="$T"
    printf '{"ts": %d, "outcome": "payment_timeout", "amount_msat": 50000}\n' "$(( $(date +%s) - 7200 ))" > "$DVM_LEDGER"
    dvm_heartbeat
    check "stale ledger is marked STALE"     "$(cat $T)" "STALE"
    check "stale ledger keeps the event"     "$(cat $T)" "dvm: payment_timeout 50000msat"
    T=$(mktemp); LOG="$T"
    DVM_LEDGER="$TMP_DVM/absent.jsonl"
    dvm_heartbeat
    if [ ! -s "$T" ]; then echo "  PASS: absent ledger writes nothing"; pass=$((pass+1));
    else echo "  FAIL: absent ledger wrote: $(cat $T)"; fail=$((fail+1)); fi
    rm -rf "$TMP_DVM"

    echo "-- selftest: $pass passed, $fail failed"
    [ $fail -eq 0 ]
}

case "${1:-}" in
    --selftest) selftest ;;
    *) run_round ;;
esac
