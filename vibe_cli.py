#!/usr/bin/env python3
"""
vibe_cli — interactive puter session with vault pre-load and vibe presets.

Usage:
  vibe                          # start interactive puter REPL
  vibe --session <id>           # resume a session
  vibe --scan <dir>             # scan a directory on startup
  vibe --provider groq          # force a provider on startup
  vibe --model <model>          # force a model on startup
  vibe --help                   # show this help
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
    mapping = {
        "openrouter": "OPENROUTER_API_KEY",
        "groq": "GROQ_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "cerebras": "CEREBRAS_API_KEY",
        "zenmux": "ZENMUX_API_KEY",
        "orcarouter": "ORCAROUTER_API_KEY",
        "bazaar": "BAZAAR_API_KEY",
        "openai": "OPENAI_API_KEY",
        "jules": "JULES_API_KEY",
        "swarms": "SWARMS_API_KEY",
    }
    for provider, envvar in mapping.items():
        k = keys.get(provider, {}).get("key", "")
        if k and "PLACEHOLDER" not in k and k != "sk-test_key_placeholder":
            os.environ[envvar] = k
    pool = v.get("keys_by_provider", {}).get("openrouter", [])
    if pool:
        os.environ["OPENROUTER_API_KEY_POOL"] = ",".join(pool)


# ─── Load vault before anything else ─────────────────────────────────────────
load_vault_env()

# ─── Delegate to puter_cli ────────────────────────────────────────────────────

# Rebuild argv: extract known flags, pass rest as message args
args = sys.argv[1:]
session_id = None
scan_dir = None
force_provider = None
force_model = None
rest = []
i = 0
while i < len(args):
    if args[i] == "--session" and i + 1 < len(args):
        session_id = args[i + 1]
        i += 2
    elif args[i] == "--scan" and i + 1 < len(args):
        scan_dir = args[i + 1]
        i += 2
    elif args[i] == "--provider" and i + 1 < len(args):
        force_provider = args[i + 1]
        i += 2
    elif args[i] == "--model" and i + 1 < len(args):
        force_model = args[i + 1]
        i += 2
    else:
        rest.append(args[i])
        i += 1

puter_args = []
if session_id:
    puter_args.extend(["--session", session_id])
if scan_dir:
    puter_args.extend(["--scan", scan_dir])
if force_provider:
    puter_args.extend(["--provider", force_provider])
if force_model:
    puter_args.extend(["--model", force_model])
puter_args.extend(rest)

if not puter_args:
    # Interactive mode — exec puter_cli in-process so it takes over the terminal
    sys.argv = ["puter_cli"] + puter_args
    sys.path.insert(0, str(Path.home() / "RootBase"))
    from RootBase.puter.puter_cli import main
    main()
    sys.exit(0)

result = subprocess.run(
    [sys.executable, "-m", "RootBase.puter.puter_cli"] + puter_args,
    capture_output=True, text=True
)
sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)
sys.exit(result.returncode)
