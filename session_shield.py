#!/usr/bin/env python3
"""session_shield.py — pin oom_score_adj on the interactive session stack.

Runs as root (systemd unit session-shield.service). Every 3s it pins
oom_score_adj=-900 on any process whose command line matches the session
patterns (freebuff, gemini, tmux, interactive shells), so the kernel OOM
killer can never select the terminal/agent session while expendable daemons
(adj=800 via MemGuard) exist.

Safe by construction: only LOWERS adj toward -900, never raises anything,
never touches a process already at a lower (more protected) value.
"""
import os
import time

PATTERNS = ("freebuff", "gemini", "tmux", "bash", "zsh", "fish")
TARGET = -900
POLL = 3.0


def procs():
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw = f.read().replace(b"\x00", b" ").decode("utf-8", "replace")
            out.append((int(pid), raw))
        except (OSError, ValueError):
            continue
    return out


def get_adj(pid):
    try:
        with open(f"/proc/{pid}/oom_score_adj") as f:
            return int(f.read().strip())
    except OSError:
        return None


def set_adj(pid, value):
    try:
        with open(f"/proc/{pid}/oom_score_adj", "w") as f:
            f.write(str(value))
        return True
    except OSError:
        return False


def main():
    while True:
        for pid, cmd in procs():
            if pid == os.getpid() or not cmd or not cmd.strip():
                continue
            if any(p in cmd for p in PATTERNS):
                cur = get_adj(pid)
                if cur is not None and cur > TARGET:
                    set_adj(pid, TARGET)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
