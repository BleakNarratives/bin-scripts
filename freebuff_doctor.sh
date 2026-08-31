#!/bin/bash
# freebuff_doctor.sh — Freebuff CLI boot doctor (tmux-hosted, single-instance aware)
# The Freebuff CLI allows only ONE instance machine-wide (lock:
# ~/.config/manicode/freebuff-instance-owner.json). This script therefore:
#   - spawns the doctor in tmux ONLY if no live instance exists
#   - yields gracefully if your own session holds the lock
#
# Usage: freebuff_doctor.sh [spawn|attach|stop|status]

export PATH="$HOME/.npm-global/bin:$PATH"
DOC_DIR="$HOME/Freebuff-Doc"
SESSION="freebuff-doctor"
STAMP="$DOC_DIR/doctor_boot.log"

# boot-stamp: append-once-per-boot evidence line (survives reboot for validation)
stamp() {
    local event="$1"
    mkdir -p "$DOC_DIR"
    echo "$(date '+%Y-%m-%d %H:%M:%S') boot=$(cut -d. -f1 /proc/uptime)s $event" >> "$STAMP"
}
LOCK="$HOME/.config/manicode/freebuff-instance-owner.json"

owner_alive() {
    [ -f "$LOCK" ] || return 1
    local pid
    pid=$(python3 -c "import json;print(json.load(open('$LOCK')).get('pid',0))" 2>/dev/null)
    [ -n "$pid" ] && [ "$pid" -gt 0 ] && kill -0 "$pid" 2>/dev/null
}

case "$1" in
    attach)
        exec tmux attach -t "$SESSION"
        ;;
    stop)
        tmux kill-session -t "$SESSION" 2>/dev/null && echo "Doctor stopped." || echo "Doctor not running."
        ;;
    status)
        if tmux has-session -t "$SESSION" 2>/dev/null; then
            echo "Doctor RUNNING in tmux '$SESSION'. Attach: tmux attach -t $SESSION"
        elif owner_alive; then
            echo "Doctor idle — your own Freebuff session (pid $(python3 -c "import json;print(json.load(open('$LOCK'))['pid'])")) owns the CLI."
        else
            echo "Doctor idle, no Freebuff instance running."
        fi
        ;;
    spawn|"")
        command -v freebuff >/dev/null 2>&1 || { echo "freebuff CLI not found" >&2; exit 1; }
        # Self-heal the OOM defense stack first (swap/memguard/session-shield)
        "$HOME/bin/boot_heal.sh"
        [ -f "$DOC_DIR/CLAUDE.md" ] || { echo "missing pre-prompt: $DOC_DIR/CLAUDE.md" >&2; exit 1; }
        tmux has-session -t "$SESSION" 2>/dev/null && { echo "Doctor already running."; exit 0; }
        if owner_alive; then
            stamp "skipped: lock held by pid $(python3 -c "import json;print(json.load(open('$LOCK')).get('pid',0))")"
            echo "Freebuff busy (your session owns it) — doctor skipped."
            exit 0
        fi
        rm -f "$LOCK"   # stale lock from a crashed session
        tmux new-session -d -s "$SESSION" -c "$DOC_DIR" freebuff --cwd "$DOC_DIR"
        stamp "spawned tmux='$SESSION' ppid=$PPID src=${FREEBUFF_DOCTOR_SRC:-bashrc}"
        echo "Freebuff doctor launched (tmux '$SESSION'). Attach: tmux attach -t $SESSION"
        # TTL watchdog: the Freebuff CLI stalls at the model picker and never
        # auto-runs the pre-prompt, leaking ~192MB RAM. Kill the session if it's
        # still alive (no report) after DOCTOR_TTL_SEC (default 600). Update the
        # Freebuff-Doc/CLAUDE.md pre-prompt to write a report file if you need
        # the watchdog to distinguish "done" from "stalled".
        (
            sleep "${DOCTOR_TTL_SEC:-600}"
            if tmux has-session -t "$SESSION" 2>/dev/null; then
                tmux kill-session -t "$SESSION" 2>/dev/null
                stamp "watchdog killed stalled doctor after ${DOCTOR_TTL_SEC:-600}s"
            fi
        ) &
        disown 2>/dev/null
        ;;
    *)
        echo "Usage: $0 {spawn|attach|stop|status}"; exit 1 ;;
esac
