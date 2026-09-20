#!/usr/bin/env python3

# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: armory
# DEPS: os,sys
# ROLE: Thin wrapper ensuring GEMINI_API_KEY loads from vault
# AUTHOR: Bleak
# SESSION: 2026-09-11
# TIER: 4
# [/DNA_TAG]

"""
gemini_cli — thin wrapper that ensures GEMINI_API_KEY is loaded from vault
before delegating to the system `gemini` CLI.

Usage:
  gemini_cli -p "your prompt here"
  gemini_cli --chat
  gemini_cli --version
  (any flags that the system gemini CLI supports)
"""

import os
import sys
import subprocess
from pathlib import Path

VAULT_JSON = Path.home() / ".concierge" / "vault.json"


def load_vault_env():
    """Load vault.json keys into os.environ (same logic as vault-env)."""
    if not VAULT_JSON.exists():
        return
    import json
    v = json.loads(VAULT_JSON.read_text())
    keys = v.get("keys", {})
    gemini_key = keys.get("gemini", {}).get("key", "")
    if gemini_key and "PLACEHOLDER" not in gemini_key and gemini_key != "sk-test_key_placeholder":
        os.environ["GEMINI_API_KEY"] = gemini_key


# ─── Load vault before anything else ─────────────────────────────────────────
load_vault_env()

# ─── Verify key present ───────────────────────────────────────────────────────
if not os.environ.get("GEMINI_API_KEY"):
    print("[gemini_cli] WARNING: GEMINI_API_KEY not found in vault.",
          file=sys.stderr)
    print("[gemini_cli] The system gemini CLI may not authenticate.",
          file=sys.stderr)

# ─── Delegate to system gemini CLI ───────────────────────────────────────────
gemini_bin = os.environ.get("GEMINI_BIN") or "gemini"
if not os.path.isabs(gemini_bin):
    which_result = subprocess.run(
        ["which", gemini_bin], capture_output=True, text=True
    )
    if which_result.returncode == 0:
        gemini_bin = which_result.stdout.strip()

sys.argv[0] = "gemini_cli"
result = subprocess.run(
    [gemini_bin] + sys.argv[1:],
    capture_output=True, text=True, timeout=300
)
sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)
sys.exit(result.returncode)
