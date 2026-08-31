#!/bin/bash
# cache_guard.sh — keep regenerable caches from eating the disk
#
# Checks each target dir's size; if it exceeds the limit, purges it.
# Designed for build caches that regrow (cargo target dirs etc.) —
# deletion is safe, next build just recompiles.
#
# Usage: cache_guard.sh [limit_gb] [dir...]
#   limit_gb   threshold per dir, default 3 (GB)
#   dirs       default watch list:
#                ~/.cache/berd-dev            cargo build artifacts
#                ~/.local/share/pnpm/store    pnpm package store
#                ~/.cargo/registry            crate downloads
#
# Wired into bin/round.sh so it runs every 10-minute round.

LIMIT_GB=${1:-3}
shift || true
FORCE=0
# --memory mode: purge everything on the watch list regardless of size
# (used when MemGuard signals memory pressure, not disk pressure).
[ "$LIMIT_GB" = "--memory" ] && { FORCE=1; LIMIT_GB=3; }
DIRS=("$@")
[ ${#DIRS[@]} -eq 0 ] && DIRS=(
    "$HOME/.cache/berd-dev"
    "$HOME/.local/share/pnpm/store"
    "$HOME/.cargo/registry"
    "$HOME/.npm/_cacache"
    "$HOME/.cache/pip"
)

LOG=${CACHE_GUARD_LOG:-$HOME/rounds.log}
LIMIT_MB=$((LIMIT_GB * 1024))

for d in "${DIRS[@]}"; do
    [ -d "$d" ] || continue
    mb=$(du -sm "$d" 2>/dev/null | cut -f1)
    [ -z "$mb" ] && continue
    if [ "$FORCE" = 1 ] || [ "$mb" -ge "$LIMIT_MB" ]; then
        echo "$(date -Iseconds), cache_guard, $d at ${mb}MB >= ${LIMIT_MB}MB limit — purging" >> "$LOG"
        chmod -R u+w "$d" 2>/dev/null   # some caches ship read-only stubs
        rm -rf "$d"
        echo "$(date -Iseconds), cache_guard, PURGED $d" >> "$LOG"
    fi
done
