#!/bin/bash
# boot_heal.sh — silent self-heal of the OOM defense stack at boot.
# Checks and fixes, in order:
#   1. swapfile active            (swapon if missing from /proc/swaps)
#   2. memguard.service running   (user unit — the guardian)
#   3. session-shield.service running (root unit — adj pinning)
#   4. sysctl tuning file present (/etc/sysctl.d/90-crostini-memory.conf)
# Logs one line per action to ~/MikeySwarm/logs/boot_heal.log
LOG="$HOME/MikeySwarm/logs/boot_heal.log"
mkdir -p "$(dirname "$LOG")"
log() { echo "$(date -Iseconds) $*" >> "$LOG"; }

if ! grep -q "^/swapfile" /proc/swaps 2>/dev/null; then
    sudo swapon /swapfile 2>/dev/null && log "FIXED: swapon /swapfile" \
        || log "WARN: swapon /swapfile failed"
fi

if ! systemctl --user is-active memguard >/dev/null 2>&1; then
    systemctl --user start memguard && log "FIXED: memguard started" \
        || log "WARN: memguard start failed"
fi

if ! systemctl is-active session-shield >/dev/null 2>&1; then
    sudo systemctl start session-shield && log "FIXED: session-shield started" \
        || log "WARN: session-shield start failed"
fi

if [ ! -f /etc/sysctl.d/90-crostini-memory.conf ]; then
    log "WARN: sysctl tuning file missing (/etc/sysctl.d/90-crostini-memory.conf)"
fi

log "boot_heal ran (uptime $(cut -d. -f1 /proc/uptime)s)"
