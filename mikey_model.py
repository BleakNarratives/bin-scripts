#!/usr/bin/env python3

# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: swarm
# DEPS: json,os,subprocess,sys
# ROLE: Self-contained survival stack for Bleak Narratives
# AUTHOR: Bleak
# SESSION: 2026-09-11
# TIER: 3
# [/DNA_TAG]

"""
MIKEY_MODEL - Self-contained survival stack for Bleak Narratives.
CLI: start | status | charge <amount> [user] | round | hook <payload>
No external deps. Logs to ~/mikey_model.log.
"""
import os, sys, subprocess, json, time, shlex
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

HOME = str(Path.home())
LOG = HOME + "/mikey_model.log"

def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{time.ctime()}: {msg}\n")

def start():
    # Payment engine
    subprocess.Popen(["bash", "-c", "~/bin/master_control.sh start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Hook server (if not running)
    if not check_port(9090):
        subprocess.Popen(["python3", HOME+"/bin/hook_server.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Round cron already set
    log("Started all services.")
    print("All services started. Use 'status' to verify.")

def status():
    out = subprocess.run(["bash", "-c", "~/bin/master_control.sh status"], capture_output=True, text=True).stdout.strip()
    print(out)
    hook = "running" if check_port(9090) else "stopped"
    print(f"Hook server: {hook}")
    # Check cron
    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if "round.sh" in cron.stdout:
        print("Cron for rounds: active")
    else:
        print("Cron for rounds: not set")

def charge(args):
    amount = args[0] if args else "10"
    user = args[1] if len(args)>1 else "mikey"
    cmd = f"~/bin/master_control.sh charge {shlex.quote(amount)} {shlex.quote(user)}"
    out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    print(out.stdout.strip() or out.stderr.strip())

def run_round():
    subprocess.run(["bash", "-c", "~/bin/round.sh"], check=False)
    print("Round executed. Check ~/rounds.log")

def hook_trigger(payload):
    # Send POST to hook server
    cmd = ["curl", "-s", "-X", "POST", "http://localhost:9090/hook",
           "-H", "Content-Type: application/json", "-d", payload]
    subprocess.run(cmd, check=False)
    print("Hook triggered.")

def check_port(port):
    # Simple socket check
    import socket
    s = socket.socket()
    try:
        s.connect(("localhost", port))
        s.close()
        return True
    except:
        return False

def main():
    if len(sys.argv) < 2:
        print("Usage: mikey_model.py {start|status|charge [amount] [user]|round|hook <json>}")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "start":
        start()
    elif cmd == "status":
        status()
    elif cmd == "charge":
        charge(sys.argv[2:])
    elif cmd == "round":
        run_round()
    elif cmd == "hook":
        if len(sys.argv) < 3:
            print("Usage: hook <json_payload>")
        else:
            hook_trigger(sys.argv[2])
    else:
        print("Unknown command")

if __name__ == "__main__":
    main()
