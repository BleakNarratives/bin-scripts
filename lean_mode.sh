#!/bin/bash
# lean_mode.sh — stop/start non-essential background daemons to free RAM
# during interactive agent sessions. All stopped units are restart-safe
# (systemd Restart=always) and come back with `lean_mode off`.
#
# Handshake with the Overseer Daemon (Layer 4): `on` writes
# state/overseer_guard_skip.json listing the units it stopped so the
# daemon's guard does NOT "helpfully" restart them mid-session; `off`
# removes the file and guard coverage resumes.
#
# Usage: lean_mode.sh {on|off|status}
#
# Frees roughly 150-250MB baseline on this VM (2.6GB total).

UNITS=(
    arena-bridge.service
    crash-feeder.service
    city-api.service
    dvm-worker.service
    payment-engine.service
    implore-research.service
)
LOG="$HOME/MikeySwarm/logs/lean_mode.log"
SKIP="$HOME/MikeySwarm/state/overseer_guard_skip.json"
mkdir -p "$(dirname "$LOG")" "$(dirname "$SKIP")"

avail() { free -m | awk '/Mem:/{print $7}'; }

case "$1" in
    on)
        echo "$(date -Iseconds) lean ON (avail $(avail)MB)" >> "$LOG"
        stopped=()
        for u in "${UNITS[@]}"; do
            if systemctl --user is-active "$u" >/dev/null 2>&1; then
                systemctl --user stop "$u"
                stopped+=("$u")
                echo "$(date -Iseconds)   stopped $u" >> "$LOG"
            fi
        done
        python3 -c 'import json,sys; open(sys.argv[1],"w").write(json.dumps({"units": sys.argv[2:], "ts": __import__("time").time()}))' "$SKIP" "${stopped[@]}"
        echo "lean mode ON — daemons stopped, $(avail)MB available"
        ;;
    off)
        echo "$(date -Iseconds) lean OFF (avail $(avail)MB)" >> "$LOG"
        rm -f "$SKIP"
        for u in "${UNITS[@]}"; do
            systemctl --user start "$u"
            echo "$(date -Iseconds)   started $u" >> "$LOG"
        done
        echo "lean mode OFF — daemons restored, $(avail)MB available"
        ;;
    status)
        for u in "${UNITS[@]}"; do
            printf "%-28s %s\n" "$u" "$(systemctl --user is-active "$u" 2>/dev/null)"
        done
        echo "available: $(avail)MB"
        ;;
    *)
        echo "usage: lean_mode.sh {on|off|status}" >&2
        exit 1
        ;;
esac
