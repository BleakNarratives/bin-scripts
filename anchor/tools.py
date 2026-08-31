import subprocess
import os

def run_shell(cmd):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return res.stdout if res.returncode == 0 else res.stderr
    except Exception as e:
        return str(e)

def read_file(path):
    try:
        with open(os.path.expanduser(path), 'r') as f:
            return f.read()
    except Exception as e:
        return f"Error reading {path}: {str(e)}"

def live_bug_scan():
    return []
