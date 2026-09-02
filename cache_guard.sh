#!/bin/bash
# cache_guard.sh — keep regenerable caches from eating the disk
#
# Two layers of defense:
#   1. Per-dir purge: any watch-list dir over the per-dir limit gets purged.
#   2. GLOBAL DISK FLOOR: if free space on / drops below DISK_FLOOR_GB,
#      watch-list dirs are purged largest-first regardless of per-dir
#      limits until the floor is restored. This is the 2026-09-01 lesson —
#      yarn (5.1G) + berd target (6.9G) + buzz target (5.4G) grew past the
#      old watch list and pushed the disk to 99% with only 1.3G free.
#
# Designed for build caches that regrow — deletion is safe, next build
# just recompiles. Never touches source, git history, node_modules, or data.
#
# Usage: cache_guard.sh [limit_gb] [dir...]
#   limit_gb   threshold per dir, default 3 (GB)
#   dirs       default watch list (see below)
#   --memory   purge everything on the watch list regardless of size
#              (used when MemGuard signals memory pressure, not disk)
#
# Env:
#   CACHE_GUARD_DISK_FLOOR_GB   free-space floor, default 5 (GB)
#
# Wired into bin/round.sh so it runs every 10-minute round.

LIMIT_GB=${1:-3}
shift || true
FORCE=0
[ "$LIMIT_GB" = "--memory" ] && { FORCE=1; LIMIT_GB=3; }
DIRS=("$@")
[ ${#DIRS[@]} -eq 0 ] && DIRS=(
    "$HOME/.cache/yarn"
    "$HOME/.cache/berd-dev"
    "$HOME/.cache/uv"
    "$HOME/berd/src-tauri/target"
    "$HOME/buzz/target"
    "$HOME/.local/share/pnpm/store"
    "$HOME/.cargo/registry"
    "$HOME/.npm/_cacache"
    "$HOME/.cache/pip"
)

LOG=${CACHE_GUARD_LOG:-$HOME/rounds.log}
LIMIT_MB=$((LIMIT_GB * 1024))
DISK_FLOOR_GB=${CACHE_GUARD_DISK_FLOOR_GB:-5}

log() { echo "$(date -Iseconds), cache_guard, $*" >> "$LOG"; }

purge_dir() {
    local d="$1"
    log "$d at ${2}MB — purging"
    chmod -R u+w "$d" 2>/dev/null   # some caches ship read-only stubs
    rm -rf "$d" 2>/dev/null
    log "PURGED $d"
}

free_gb() { df -BG / | awk 'NR==2 {print $4}' | tr -d 'G'; }

dir_mb() { du -sm "$1" 2>/dev/null | cut -f1; }

# --- layer 1: per-dir limits (or --memory force) ---
for d in "${DIRS[@]}"; do
    [ -d "$d" ] || continue
    mb=$(dir_mb "$d")
    [ -z "$mb" ] && continue
    if [ "$FORCE" = 1 ] || [ "$mb" -ge "$LIMIT_MB" ]; then
        purge_dir "$d" "$mb"
    fi
done

# --- layer 2: global disk floor — purge largest-first until restored ---
avail=$(free_gb)
if [ "$avail" -lt "$DISK_FLOOR_GB" ]; then
    log "DISK FLOOR BREACH: ${avail}G free < ${DISK_FLOOR_GB}G floor — emergency purge largest-first"
    # Build "size<TAB>path" pairs for existing dirs, sort numerically desc.
    # (Tab-separated so paths with spaces survive the sort; NUL not needed —
    # watch-list paths never contain tabs/newlines.)
    pairs=()
    for d in "${DIRS[@]}"; do
        [ -d "$d" ] || continue
        mb=$(dir_mb "$d")
        [ -z "$mb" ] && continue
        pairs+=("$mb"$'\t'"$d")
    done
    while IFS=$'\t' read -r mb d; do
        avail=$(free_gb)
        [ "$avail" -ge "$DISK_FLOOR_GB" ] && break
        [ -z "$d" ] && continue
        [ "$mb" -lt 100 ] && continue   # skip noise under 100MB
        purge_dir "$d" "$mb"
    done < <(printf '%s\n' "${pairs[@]}" | sort -rn)
    avail=$(free_gb)
    log "disk floor purge complete: ${avail}G free"
fi
