#!/bin/bash
# session_beacon.sh — prove the agent session is alive by refreshing the
# crash-watchdog heartbeat (~/MikeySwarm/state/session_heartbeat).
#
# Only writes while a Freebuff agent session actually lives (pgrep
# "freebuff --continue"), so an intentional close is never re-armed.
# Called by the agent at session start AND by session-beacon.timer every
# 60s, which makes crash coverage automatic for every future session.
HB="$HOME/MikeySwarm/state/session_heartbeat"
if ! pgrep -f "freebuff --continue" >/dev/null 2>&1; then
    exit 0   # no live session — don't arm the watchdog
fi
mkdir -p "$(dirname "$HB")"
date +%s > "$HB"
