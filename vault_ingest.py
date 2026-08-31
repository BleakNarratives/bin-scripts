#!/usr/bin/env python3
"""
vault_ingest.py — parse scattered key txt files and merge into ~/.concierge/vault.json

Reads key files (mo_keys.txt, mo-keys.txt, etc.), classifies keys by prefix,
and merges new keys into the consolidated vault. Does NOT overwrite existing
keys unless --force is passed.

Usage:
  python3 ~/bin/vault_ingest.py                  # scan and ingest
  python3 ~/bin/vault_ingest.py --dry-run        # preview only
  python3 ~/bin/vault_ingest.py --force          # overwrite existing
"""

import json
import os
import re
import sys
from pathlib import Path

VAULT = Path.home() / ".concierge" / "vault.json"

# Key prefix -> provider mapping
PREFIX_MAP = [
    # (regex, provider, env_var_name, source_label)
    (r"sk-or-v1-", "openrouter", "OPENROUTER_API_KEY", "mo_keys"),
    (r"sk-orca-", "orcarouter", "ORCAROUTER_API_KEY", "mo_keys"),
    (r"sk-bl-", "bazaar", "BAZAAR_API_KEY", "mo_keys"),
    (r"sk-bl-od-", "bazaar", "BAZAAR_API_KEY", "mo_keys"),
    (r"sk-bl-ypw-", "bazaar", "BAZAAR_API_KEY", "mo_keys"),
    (r"sk-mg-v1-", "zenmux", "ZENMUX_API_KEY", "mo_keys"),
    (r"sk-aa52", "swarms", "SWARMS_API_KEY", "mo_keys"),
    (r"sk-proj-", "openai", "OPENAI_API_KEY", "mo_keys"),
    (r"gsk_", "groq", "GROQ_API_KEY", "mo_keys"),
    (r"csk-", "cerebras", "CEREBRAS_API_KEY", "mo_keys"),
    (r"csk-dpvet6", "cerebras", "CEREBRAS_API_KEY", "mo_keys"),
    (r"AQ\.Ab8RN6J", "jules", "JULES_API_KEY", "mo_keys"),
    (r"AQ\.Ab8RN6K", "jules", "JULES_API_KEY", "mo_keys"),
    (r"AQ\.Ab8RN6L", "jules", "JULES_API_KEY", "mo_keys"),
    (r"AIzaSy", "gemini", "GEMINI_API_KEY", "mo_keys"),
]

# Files to scan (in priority order)
SCAN_FILES = [
    Path.home() / "mo_keys.txt",
    Path.home() / "mo-keys.txt",
    Path.home() / "The-Werkz" / "Official-Vertical-AI-Boardroom" / "mo_keys.txt",
    Path.home() / "opernrouter_api_vault_valet_concierge.txt",
]

# Also scan any *.txt file in ~/The-Werkz/ that has "key" in the name
for p in (Path.home() / "The-Werkz").rglob("*key*.txt"):
    if p not in SCAN_FILES:
        SCAN_FILES.append(p)


def classify_key(token: str) -> tuple:
    """Classify a token by prefix. Returns (provider, env_var, source) or None."""
    token = token.strip()
    if not token or len(token) < 20:
        return None
    # Skip placeholders
    if "PLACEHOLDER" in token or "test_key" in token:
        return None
    for pattern, provider, env_var, source in PREFIX_MAP:
        if re.match(pattern, token):
            return (provider, env_var, source)
    return None


def extract_keys_from_file(path: Path) -> list:
    """Extract all API-key-like tokens from a text file."""
    if not path.exists():
        return []
    text = path.read_text(errors="ignore")
    # Match common key patterns
    patterns = [
        r"sk-[a-zA-Z0-9_\-]{20,}",
        r"gsk_[a-zA-Z0-9]{20,}",
        r"csk-[a-zA-Z0-9]{20,}",
        r"AQ\.Ab8RN6[a-zA-Z0-9_\-]{20,}",
        r"AIzaSy[a-zA-Z0-9_\-]{20,}",
    ]
    keys = []
    for pat in patterns:
        for m in re.finditer(pat, text):
            k = m.group(0).strip()
            if k not in keys:
                keys.append(k)
    return keys


def load_vault() -> dict:
    if VAULT.exists():
        return json.loads(VAULT.read_text())
    return {"scanned_keys": 0, "live_providers": [], "keys": {}, "keys_by_provider": {}}


def save_vault(vault: dict):
    VAULT.parent.mkdir(parents=True, exist_ok=True)
    vault["consolidated_at"] = __import__("datetime").datetime.now().isoformat()
    VAULT.write_text(json.dumps(vault, indent=2))


def main():
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    vault = load_vault()
    if "keys" not in vault:
        vault["keys"] = {}
    if "keys_by_provider" not in vault:
        vault["keys_by_provider"] = {}

    existing = vault.get("keys", {})
    # Track all keys per provider for the multi-key pool
    pools = vault.get("keys_by_provider", {})

    all_found = []
    new_count = 0
    skip_count = 0

    for f in SCAN_FILES:
        if not f.exists():
            continue
        keys = extract_keys_from_file(f)
        for k in keys:
            result = classify_key(k)
            if not result:
                continue
            provider, env_var, source = result
            all_found.append((provider, k, f.name))

            # Add to pool (all keys for rotation/failover)
            if provider not in pools:
                pools[provider] = []
            if k not in pools[provider]:
                pools[provider].append(k)
                new_count += 1
            else:
                skip_count += 1

            # Set as primary key if not present (or if force)
            if provider not in existing or force:
                existing[provider] = {
                    "source": f"mo_keys:{f.name}",
                    "prefix": k[:16] + "...",
                    "key": k,
                    "env_var": env_var,
                }

    vault["keys"] = existing
    vault["keys_by_provider"] = pools
    vault["scanned_keys"] = sum(len(v) for v in pools.values())

    # Update live_providers list
    vault["live_providers"] = sorted(pools.keys())

    # Report
    print(f"=== Vault Ingest {'(DRY RUN)' if dry_run else ''} ===")
    print(f"Files scanned: {len([f for f in SCAN_FILES if f.exists()])}")
    print(f"Keys found: {len(all_found)}")
    print(f"New keys added to pools: {new_count}")
    print(f"Duplicate keys skipped: {skip_count}")
    print(f"\nProvider pools:")
    for provider, keys in sorted(pools.items()):
        primary = existing.get(provider, {}).get("key", "")
        primary_marker = " (primary)" if primary else ""
        print(f"  {provider:15s} {len(keys)} key(s){primary_marker}")
        for k in keys:
            marker = " *" if k == primary else ""
            print(f"    {k[:20]}...{k[-4:]}{marker}")

    if not dry_run:
        save_vault(vault)
        print(f"\nVault saved: {VAULT}")
    else:
        print(f"\n(dry run — vault not saved)")


if __name__ == "__main__":
    main()
