#!/usr/bin/env bash
# Read-only canonical CLI path report. Does not modify PATH or execute CLIs.
set -u

printf '%s\n' 'Canonical CLI paths (first existing entry wins):'
printf '%s\n' '  codex  ~/RootBase/bin/codex'
printf '%s\n' '  aider  ~/.local/bin/aider'
printf '%s\n' '  vibe   ~/.local/bin/vibe'
printf '%s\n' '  puter  ~/RootBase/bin/puter'
printf '%s\n' '  anchor ~/RootBase/bin/anchor (Puter alias)'
printf '%s\n' '  ox     ~/bin/ox (Python CLI)'
printf '%s\n' '  sageox ~/.local/bin/sageox (SageOx CLI)'
printf '%s\n' '  gemini ~/.npm-global/bin/gemini'
printf '\n%s\n' 'Distinct PATH collisions:'

for name in codex aider vibe puter anchor ox sageox gemini; do
  entries=()
  IFS=: read -ra dirs <<< "${PATH:-}"
  seen_dirs=()
  for directory in "${dirs[@]}"; do
    [[ -n "$directory" ]] || continue
    duplicate=0
    for seen in "${seen_dirs[@]}"; do [[ "$seen" == "$directory" ]] && duplicate=1; done
    [[ "$duplicate" == 1 ]] && continue
    seen_dirs+=("$directory")
    candidate="$directory/$name"
    [[ -x "$candidate" ]] || continue
    resolved="$(readlink -f "$candidate" 2>/dev/null || printf '%s' "$candidate")"
    already=0
    for existing in "${entries[@]}"; do [[ "$existing" == "$resolved" ]] && already=1; done
    [[ "$already" == 0 ]] && entries+=("$resolved")
  done
  if [[ "${#entries[@]}" -gt 1 ]]; then
    printf '  %s: %s\n' "$name" "${entries[*]}"
  fi
done
