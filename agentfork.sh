#!/usr/bin/env bash
# agentfork.sh — fork a coding task to a sub-agent CLI inside an isolated tmux
# session, poll for a <<COMPLETE>> sentinel, and collect the output.
#
# The machine is memory-bound (2.6Gi total) and freebuff is single-instance
# (lock held by the parent). So "sub-agents" = qwen / codex / goose / ox /
# aider, each launched in its OWN tmux session (never panes inside the live
# session). Supplies each with a task file typed in via send-keys, and asks
# for a sentinel so the parent can poll rather than babysit.
#
# Usage:
#   agentfork.sh start <name> [--agent qwen|codex|goose|ox|aider] [-m MODEL] [--max-secs N]
#   agentfork.sh start <name> --style file|argv --task-file <f>|--prompt "<p>"
#       --style file (default): type the task file content into the CLI's prompt.
#       --style argv   : append the prompt/task as a command-line argument.
#   agentfork.sh status [name]       RUNNING/DONE/… (all if no name)
#   agentfork.sh collect <name>      tail the fork log
#   agentfork.sh list
#   agentfork.sh attach <name>       Ctrl-b d to detach back
#   agentfork.sh kill <name>
#   agentfork.sh capture <name>      copy full log to STDOUT, mark done
#
# Output per fork: ~/MikeySwarm/logs/forks/<name>.out (teed live).
# Env: FORK_MAX_TASKS (3), FORK_MIN_AVAIL_MB (500).
###############################################################################

set -euo pipefail

LOG_ROOT="${FORK_LOG_ROOT:-$HOME/MikeySwarm/logs/forks}"
MAX_TASKS="${FORK_MAX_TASKS:-3}"
MIN_AVAIL="${FORK_MIN_AVAIL_MB:-500}"
mkdir -p "$LOG_ROOT"

cli_for() {
  case "$1" in
    qwen)   echo "/home/bleaknarratives/.local/bin/qwen" ;;
    codex)  echo "/home/bleaknarratives/RootBase/bin/codex" ;;
    goose)  echo "/home/bleaknarratives/bin/goose" ;;
    ox)     echo "/home/bleaknarratives/.local/bin/ox" ;;
    aider)  echo "/home/bleaknarratives/.local/bin/aider" ;;
    *)      echo "/home/bleaknarratives/.local/bin/qwen" ;;
  esac
}

# Real fork-target capability matrix (verified 2026-08-31):
#   aider      — FILE-TOOLING (reads/edits code) via live Groq. PRIMARY fork target.
#               Needs --model groq/openai/gpt-oss-120b, --file <paths> (aider only
#               reads files explicitly added), and the env sourced for API keys.
#   goose      — file-tooling enabled in config but NO provider configured (interactive-only 'goose configure').
#   qwen       — CHAT-ONLY cloud shim, NO file access, hallucinates paths. Do NOT
#               use for any task that must touch the filesystem.
#   codex      — binary chmod-permission-denied (not yet executable).
#   ox          — present, untested non-interactive.
ENV_SOURCE="$HOME/.config/environment.sh"
AIDER_MODEL="groq/openai/gpt-oss-120b"

mem_ok() {
  local avail_mb
  avail_mb=$(awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo)
  [[ "${avail_mb:-0}" -ge "$MIN_AVAIL" ]]
}

fork_running() { tmux has-session -t "fork-$1" 2>/dev/null; }

count_forks() { tmux ls 2>/dev/null | grep -c '^fork-' || true; }

start() {
  local name="" agent="aider" style="" taskfile="" prompt="" model="$AIDER_MODEL" \
        files="" maxsecs="1500"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --agent) agent="${2:-}"; shift 2;;
      --file) files="${files:+$files }$2"; shift 2;;
      --style) style="${2:-}"; shift 2;;
      --task-file) taskfile="${2:-}"; shift 2;;
      --prompt) prompt="${2:-}"; shift 2;;
      -m) model="${2:-}"; shift 2;;
      --max-secs) maxsecs="${2:-1500}"; shift 2;;
      *) name="$1"; shift;;
    esac
  done
  [[ -n "$name" ]] || { echo "usage: agentfork.sh start <name> [opts]" >&2; exit 2; }
  if fork_running "$name"; then echo "fork '$name' already running" >&2; exit 1; fi
  if ! mem_ok; then
    echo "BLOCKED: MemAvailable < ${MIN_AVAIL}MB. lean_mode.sh on or wait." >&2
    exit 75
  fi
  local running; running=$(count_forks)
  if (( running >= MAX_TASKS )); then
    echo "BLOCKED: already ${running} forks (>= FORK_MAX_TASKS=$MAX_TASKS)." >&2; exit 75
  fi

  # Build the task body.
  local body=""
  if [[ -n "$taskfile" ]]; then
    if [[ ! -f "$taskfile" ]]; then echo "taskfile not found: $taskfile" >&2; exit 2; fi
    body+="== TASK FILE: $taskfile ==\n"
    body+="$(cat "$taskfile")\n"
  fi
  [[ -n "$prompt" ]] && body+="$prompt\n"
  body+="\n[INSTRUCTIONS] Complete the task above, working in the codebase as needed. When the task is FULLY complete, end your reply with the exact final line:  <<COMPLETE>>\n"

  local task="$LOG_ROOT/$name.task" out="$LOG_ROOT/$name.out"
  printf '%b' "$body" > "$task"
  : > "$out"

  local cli; cli=$(cli_for "$agent")
  [[ -x "$cli" ]] || { echo "cli not executable: $cli" >&2; exit 2; }

  local payload; payload="$(cat "$task")"
  local newest="$cli"

  if [[ "$agent" == "aider" ]]; then
    # aider is a one-shot, file-tooling agent: source the API keys, model, add
    # the task's file paths so aider can READ them, and pass the body via --message.
    local envpre=""
    [[ -f "$ENV_SOURCE" ]] && envpre="source '$ENV_SOURCE'; export GROQ_API_KEY OPENROUTER_API_KEY; "
    local flist=()
    for f in $files; do flist+=(--file "$f"); done
    local help_flags="--no-auto-commits --no-show-model-warnings --yes"
    local edge_flags="--message"
    # build the one-shot command: env; aider model file(s) -m '<body>'
    tmux new-session -d -s "fork-$name" \
      "cd $PWD; ${envpre}exec $cli --model '$model' ${flist[*]:+${flist[*]} }$help_flags --message $(printf '%q' "$payload") 2>&1 | tee -a '$out'; echo '== FORK EXIT ==' >> '$out'"
    echo "• fork '$name' spawned (agent=aider cli=$cli model=$model)"
    echo "  task : $task   out : $out   files: ${flist[*]:-none}"
  else
    # Style-based dispatch for the interactive/TUI or argv-shim agents.
    if [[ -z "$style" ]]; then
      style="$([[ "$agent" == "qwen" ]] && echo argv || echo file)"
    fi
    local launch_cmd
    if [[ "$style" == "argv" ]]; then
      launch_cmd="$cli ${model:+-m $model} $(printf '%q' "$payload")"
    else
      launch_cmd="$cli ${model:+-m $model}"   # interactive; we send-keys the file
    fi
    tmux new-session -d -s "fork-$name" "cd $PWD; $launch_cmd 2>&1 | tee -a '$out'; echo '== FORK EXIT ==' >> '$out'"
    if [[ "$style" == "file" ]]; then
      sleep 2
      tmux send-keys -t "fork-$name" -l "$payload"
      tmux send-keys -t "fork-$name" Enter
    fi
    echo "• fork '$name' spawned (agent=$agent cli=$cli style=$style)"
    echo "  task : $task   out : $out"
  fi
  echo "  attach: agentfork.sh attach $name"
  chmod +x "$cli" 2>/dev/null || true

  # Timeout watchdog: kill the session, stamp a marker, if still running.
  (
    sleep "$maxsecs"
    if fork_running "$name" && ! grep -q '<<COMPLETE>>' "$out" 2>/dev/null; then
      echo "== TIMEOUT after ${maxsecs}s ==" >> "$out"
      tmux send-keys -t "fork-$name" C-c 2>/dev/null
      sleep 2; tmux kill-session -t "fork-$name" 2>/dev/null || true
    fi
  ) &
}

status() {
  if [[ $# -eq 0 ]]; then
    # Enumerate from the log dir so completed forks (whose tmux session has
    # exited and vanished) still appear.
    local found=0 n
    for f in "$LOG_ROOT"/*.out; do [[ -e "$f" ]] || continue; n=$(basename "$f" .out); status "$n"; found=1; done
    # plus any live session not in logs yet
    for s in $(tmux ls 2>/dev/null | grep '^fork-' | cut -d: -f1 | sed s/^fork-//); do
      [[ -e "$LOG_ROOT/$s.out" ]] || { status "$s"; found=1; }
    done
    [[ $found -eq 1 ]] || echo "  (no forks recorded)"
    return 0
  fi
  local name="$1"
  if ! fork_running "$name"; then
    if [[ -s "$LOG_ROOT/$name.out" ]]; then echo "  $name   done   $LOG_ROOT/$name.out"; else echo "  $name   missing"; fi
    return 0
  fi
  if grep -q '<<COMPLETE>>' "$LOG_ROOT/$name.out" 2>/dev/null; then
    echo "  $name   DONE(sentinel)  $LOG_ROOT/$name.out"
  else
    echo "  $name   RUNNING"
  fi
}

list() {
  echo "Forks: (out=$LOG_ROOT, max=$MAX_TASKS, min_mem=${MIN_AVAIL}MB)"
  status
  awk '/MemAvailable/ {printf "  mem avail: %.0f MB\n", $2/1024}' /proc/meminfo
}

collect() { local n="$1"; [[ -f "$LOG_ROOT/$n.out" ]] && tail -n 400 "$LOG_ROOT/$n.out" || echo "no log: $n"; }
capture() { local n="$1"; [[ -f "$LOG_ROOT/$n.out" ]] && cat "$LOG_ROOT/$n.out" || echo "no log: $n"; }
attach() { tmux attach -t "fork-$1"; }
kill_fork() { tmux send-keys -t "fork-$1" C-c 2>/dev/null; tmux kill-session -t "fork-$1" 2>/dev/null || true; }

case "${1:-}" in
  start)   shift; start "$@";;
  status)  shift; status "$@";;
  collect) n="${2:-}"; collect "$n";;
  capture) n="${2:-}"; capture "$n";;
  list)    list;;
  attach)  shift; attach "$@";;
  kill)    shift; kill_fork "$@";;
  *) echo "usage: agentfork.sh {start|status|collect|capture|list|attach|kill}" >&2; exit 2;;
esac