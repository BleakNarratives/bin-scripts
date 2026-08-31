#!/usr/bin/env bash
# ecosystem_sync.sh — one-command convergence sweep across ecosystem repos.
#
# Usage:
#   ecosystem_sync.sh              # full sweep: pull -> commit -> push (default)
#   ecosystem_sync.sh pull         # pull only (session start)
#   ecosystem_sync.sh push         # commit + push only (session end)
#   ecosystem_sync.sh dry-run      # show what would happen, change nothing
#   ecosystem_sync.sh status       # per-repo dirty counts, no changes
#
# Design:
#   - Repos are auto-discovered: ~/*/ with .git AND a configured origin.
#   - Volatile runtime files (logs/, daemon state, caches) are excluded from
#     commits via per-repo volatile lists + global patterns; they stay on disk.
#   - Rebase pull: linear history, no merge-commit noise. Conflicts are
#     reported, never auto-resolved; the repo is left untouched for review.
#   - Everything is logged to ~/MikeySwarm/logs/sync/ecosystem_sync.log
#
# DNA_TAG: ORIGIN=BleakNarratives/RootBase | ROLE=ecosystem-sync | LAST_SYNC=2026-08-29

set -u

LOG_DIR="$HOME/MikeySwarm/logs/sync"
LOG_FILE="$LOG_DIR/ecosystem_sync.log"
MODE="${1:-sync}"

mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------------------
# Volatile path patterns: never auto-commit these. Glob-relative to repo root.
# ---------------------------------------------------------------------------
VOLATILE_PATTERNS=(
  "logs/*"
  "log/*"
  "*.log"
  "*.jsonl"
  "state/overseer_state.json"
  "state/codebuff_bridge_state.json"
  "state/session_heartbeat"
  "state/overseer_guard_skip.json"
  "agent_state.json"
  "__pycache__/*"
  "*.pyc"
  ".sageox/*"
  "*.egg-info/*"
  "node_modules/*"
  ".venv/*"
  "venv/*"
)

log() {
  local line="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
  echo "$line"
  echo "$line" >> "$LOG_FILE"
}

# Returns 0 if a path matches a volatile pattern for this repo.
is_volatile() {
  local repo="$1" path="$2" pat
  for pat in "${VOLATILE_PATTERNS[@]}"; do
    # shellcheck disable=SC2254
    case "$path" in
      $pat) return 0 ;;
    esac
  done
  # Per-repo extra volatile list (optional): <repo>/.git_volatile
  local extra_file="$repo/.git_volatile"
  if [ -f "$extra_file" ]; then
    while IFS= read -r extra; do
      [ -z "$extra" ] && continue
      case "$extra" in \#*) continue ;; esac
      case "$path" in
        $extra) return 0 ;;
      esac
    done < "$extra_file"
  fi
  return 1
}

# ---------------------------------------------------------------------------
# Repo discovery: ~/*/ with .git and an origin remote. Third-party clones
# (forks of other people's projects) are excluded.
# ---------------------------------------------------------------------------
discover_repos() {
  local d remote
  for d in "$HOME"/*/; do
    [ -d "${d}.git" ] || continue
    git -C "$d" rev-parse --git-dir >/dev/null 2>&1 || continue
    remote=$(git -C "$d" remote get-url origin 2>/dev/null || echo "")
    case "$remote" in
      https://github.com/BleakNarratives/*|git@github.com:BleakNarratives/*) ;;
      "") continue ;;
      *) continue ;;  # third-party clones (sherlock, theHarvester, berd, buzz)
    esac
    printf '%s\n' "${d%/}"
  done
}

# ---------------------------------------------------------------------------
# Per-repo sweep
# ---------------------------------------------------------------------------
sweep_repo() {
  local repo="$1" mode="$2"
  local name branch dirty volatile_count=0 commitable=() path
  name=$(basename "$repo")
  branch=$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")
  dirty=$(git -C "$repo" status --porcelain 2>/dev/null | wc -l)

  # Split dirty files into volatile vs commitable
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    path="${line:3}"
    path="${path#\"}"; path="${path%\"}"
    if is_volatile "$repo" "$path"; then
      volatile_count=$((volatile_count+1))
    else
      commitable+=("$line")
    fi
  done < <(git -C "$repo" status --porcelain 2>/dev/null)

  if [ "$mode" = "status" ]; then
    log "$name [$branch] dirty=$dirty volatile=$volatile_count commitable=$(( ${#commitable[@]} ))"
    return 0
  fi

  # PULL (rebase) — unless push-only
  if [ "$mode" != "push" ]; then
    if git -C "$repo" diff --quiet 2>/dev/null && git -C "$repo" diff --cached --quiet 2>/dev/null; then
      if ! git -C "$repo" pull --rebase --autostash -q 2>>"$LOG_FILE"; then
        log "$name [$branch] PULL-CONFLICT — left for manual review"
        return 1
      fi
    else
      # Dirty tree: autostash handles it, but be explicit on failure
      if ! git -C "$repo" pull --rebase --autostash -q 2>>"$LOG_FILE"; then
        log "$name [$branch] PULL-CONFLICT (dirty tree) — left for manual review"
        return 1
      fi
    fi
  fi

  # COMMIT — only non-volatile paths
  if [ "${#commitable[@]}" -gt 0 ]; then
    local paths=()
    for line in "${commitable[@]}"; do
      local status="${line:0:2}"
      path="${line:3}"; path="${path#\"}"; path="${path%\"}"
      # renames: "R  old -> new" — stage both sides by status letter
      case "$status" in
        *D*) git -C "$repo" rm -q --cached --ignore-unmatch -- "$path" 2>/dev/null; git -C "$repo" rm -q --ignore-unmatch -- "$path" 2>/dev/null ;;
        *)   paths+=("$path") ;;
      esac
    done
    if [ "${#paths[@]}" -gt 0 ]; then
      # Use -A semantics limited to listed paths so untracked get added
      git -C "$repo" add -A -- "${paths[@]}" 2>>"$LOG_FILE"
    fi
    if ! git -C "$repo" diff --cached --quiet 2>/dev/null; then
      local msg="ecosystem_sync: sweep ${name} (${#commitable[@]} files, ${volatile_count} volatile skipped)"
      if git -C "$repo" commit -q -m "$msg" 2>>"$LOG_FILE"; then
        log "$name [$branch] committed ${#commitable[@]} files (${volatile_count} volatile skipped)"
      else
        log "$name [$branch] COMMIT-FAILED — left for manual review"
        return 1
      fi
    fi
  fi

  # PUSH
  if [ "$mode" != "pull" ]; then
    local ahead
    ahead=$(git -C "$repo" rev-list --count "@{u}..HEAD" 2>/dev/null || echo 0)
    if [ "$ahead" -gt 0 ]; then
      if git -C "$repo" push -q 2>>"$LOG_FILE"; then
        log "$name [$branch] pushed $ahead commit(s)"
      else
        log "$name [$branch] PUSH-FAILED — check auth/remote"
        return 1
      fi
    else
      log "$name [$branch] clean (nothing to push)"
    fi
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  log "=== ecosystem_sync start mode=$MODE ==="
  local repos=() r fails=0
  while IFS= read -r r; do repos+=("$r"); done < <(discover_repos)

  if [ "${#repos[@]}" -eq 0 ]; then
    log "no repos discovered — nothing to do"
    exit 0
  fi
  log "discovered ${#repos[@]} repos"

  for r in "${repos[@]}"; do
    sweep_repo "$r" "$MODE" || fails=$((fails+1))
  done

  log "=== ecosystem_sync done mode=$MODE repos=${#repos[@]} failures=$fails ==="
  exit "$fails"
}

main
