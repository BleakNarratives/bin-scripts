#!/bin/bash
# session_watchdog.sh — relaunch a crashed Freebuff agent session.
#
# Contract:
#   - The running agent writes epoch seconds to
#     ~/MikeySwarm/state/session_heartbeat each turn (session_beacon.sh).
#   - If Freebuff is alive (lock owner pid live) -> nothing to do, even if
#     the heartbeat is stale (agent idle is not a crash).
#   - If Freebuff died and the heartbeat is 90-300s stale -> crash: clear
#     the stale lock and relaunch `freebuff --continue` in tmux
#     'freebuff-agent' (matches how the user runs it; CLAUDE.md reloads).
#   - If the heartbeat is >300s stale -> intentional close: disarm.
#
# Run as a systemd USER service (session-watchdog.service); loops every 30s.

HEARTBEAT="$HOME/MikeySwarm/state/session_heartbeat"
LOCK="$HOME/.config/manicode/freebuff-instance-owner.json"
LOG="$HOME/MikeySwarm/logs/watchdog/session_watchdog.log"
SESSION="freebuff-agent"
mkdir -p "$(dirname "$HEARTBEAT")" "$(dirname "$LOG")"
export PATH="$HOME/.npm-global/bin:$PATH"

log() { echo "$(date -Iseconds) $*" >> "$LOG"; }

owner_alive() {
    [ -f "$LOCK" ] || return 1
    local pid
    pid=$(python3 -c "import json;print(json.load(open('$LOCK')).get('pid',0))" 2>/dev/null)
    [ -n "$pid" ] && [ "$pid" -gt 0 ] && kill -0 "$pid" 2>/dev/null
}

relaunch() {
    rm -f "$LOCK"
    tmux kill-session -t "$SESSION" 2>/dev/null
    tmux new-session -d -s "$SESSION" -c "$HOME" "freebuff --continue"
    date +%s > "$HEARTBEAT"
    log "RELAUNCHED freebuff --continue in tmux '$SESSION'"
}

while true; do
    if owner_alive; then
        sleep 30
        continue
    fi
    if [ ! -f "$HEARTBEAT" ]; then
        sleep 30
        continue
    fi
    now=$(date +%s)
    hb=$(cat "$HEARTBEAT")
    age=$((now - hb))
    if [ "$age" -gt 300 ]; then
        rm -f "$HEARTBEAT"
        log "heartbeat ${age}s stale -> intentional close, watchdog disarmed"
        sleep 60
        continue
    fi
    if [ "$age" -gt 90 ]; then
        log "freebuff dead, heartbeat ${age}s stale -> crash detected"
        relaunch
    fi
    sleep 30
done
