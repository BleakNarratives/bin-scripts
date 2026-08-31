#!/bin/bash
# MASTER CONTROL — Payment Engine Orchestrator
# Usage: master_control.sh [--dry-run] {start|stop|status|logs|charge [amount] [user]}
#
# The payment engine is systemd-managed when payment-engine.service exists.
# Legacy mode is supported only for environments without a user systemd unit.
# Legacy stop is strictly PID-file scoped; it never uses pkill/pkill -f.

set -u

PAYMENT_DIR="${PAYMENT_DIR:-$HOME/payment_system}"
PID_FILE="$PAYMENT_DIR/server.pid"
LOG_FILE="$PAYMENT_DIR/charges.log"
PORT="${PAYMENT_PORT:-8080}"
UNIT="${PAYMENT_UNIT:-payment-engine.service}"
DRY_RUN=0

if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=1
    shift
fi

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '[dry-run]'
        printf ' %q' "$@"
        printf '\n'
        return 0
    fi
    "$@"
}

systemd_managed() {
    systemctl --user cat "$UNIT" >/dev/null 2>&1
}

health_ok() {
    curl -s --max-time 2 "http://localhost:$PORT/health" 2>/dev/null | grep -q alive
}

pid_is_payment_engine() {
    local pid="$1" command
    [ "$pid" -gt 1 ] 2>/dev/null || return 1
    command=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
    case "$command" in
        *"$PAYMENT_DIR/main.py"*) return 0 ;;
        *) return 1 ;;
    esac
}

legacy_pid() {
    local pid
    [ -r "$PID_FILE" ] || return 1
    pid=$(tr -d '[:space:]' < "$PID_FILE")
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    pid_is_payment_engine "$pid" || return 1
    printf '%s\n' "$pid"
}

start() {
    if systemd_managed; then
        run systemctl --user start "$UNIT"
        [ "$DRY_RUN" -eq 1 ] && return 0
        sleep 1
        if systemctl --user is-active --quiet "$UNIT"; then
            echo "Payment engine started (systemd)."
        else
            echo "Payment engine failed to start. Check: journalctl --user -u $UNIT -n 20"
            return 1
        fi
    else
        if health_ok; then
            echo "Payment engine already running (legacy, port $PORT)."
            return 0
        fi
        echo "Starting payment engine on port $PORT (legacy nohup)..."
        if [ "$DRY_RUN" -eq 1 ]; then
            echo "[dry-run] nohup python3 $PAYMENT_DIR/main.py > $PAYMENT_DIR/server.log 2>&1 &"
            return 0
        fi
        nohup python3 "$PAYMENT_DIR/main.py" > "$PAYMENT_DIR/server.log" 2>&1 &
        echo $! > "$PID_FILE"
        sleep 1
        if health_ok; then
            echo "Payment engine started (legacy, pid $(cat "$PID_FILE"))."
        else
            echo "Failed to start payment engine. Check $PAYMENT_DIR/server.log"
            return 1
        fi
    fi
}

stop() {
    if systemd_managed; then
        if systemctl --user is-active --quiet "$UNIT" 2>/dev/null; then
            run systemctl --user stop "$UNIT"
            [ "$DRY_RUN" -eq 1 ] || echo "Payment engine stopped (systemd)."
        else
            echo "Payment engine not running (systemd)."
        fi
        [ "$DRY_RUN" -eq 1 ] || rm -f "$PID_FILE"
        return 0
    fi

    local pid
    if pid=$(legacy_pid); then
        run kill "$pid"
        [ "$DRY_RUN" -eq 1 ] || rm -f "$PID_FILE"
        [ "$DRY_RUN" -eq 1 ] || echo "Payment engine stopped (legacy pid $pid)."
    else
        echo "Payment engine not running (legacy; no matching PID-file process)."
        [ "$DRY_RUN" -eq 1 ] || rm -f "$PID_FILE"
    fi
}

status() {
    if systemd_managed; then
        local state
        state=$(systemctl --user is-active "$UNIT" 2>/dev/null || true)
        case "$state" in
            active)
                if health_ok; then echo "Payment engine is RUNNING on port $PORT (systemd: active)."
                else echo "Payment engine is active but health check FAILED on port $PORT."; fi ;;
            inactive) echo "Payment engine is STOPPED (systemd: inactive)." ;;
            failed) echo "Payment engine is FAILED — check: journalctl --user -u $UNIT -n 20" ;;
            *) echo "Payment engine state: $state (systemd)." ;;
        esac
    else
        if health_ok; then echo "Payment engine is RUNNING on port $PORT (legacy)."
        else echo "Payment engine is NOT running (legacy)."; fi
    fi
}

logs() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[dry-run] tail -f $LOG_FILE"
    else
        tail -f "$LOG_FILE"
    fi
}

charge() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[dry-run] POST http://localhost:$PORT/charge"
        return 0
    fi
    local amount="${1:-10}" user="${2:-anonymous}"
    curl -s -X POST "http://localhost:$PORT/charge" \
        -H "Content-Type: application/json" \
        -d "{\"amount\": $amount, \"user\": \"$user\"}" | python3 -m json.tool 2>/dev/null || \
        echo '{"amount":'"$amount"',"user":"'"$user"'","status":"error"}'
}

case "${1:-}" in
    start) start ;;
    stop) stop ;;
    status) status ;;
    logs) logs ;;
    charge) charge "${2:-}" "${3:-}" ;;
    *) echo "Usage: $0 [--dry-run] {start|stop|status|logs|charge [amount] [user]}"; exit 1 ;;
esac
