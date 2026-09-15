#!/usr/bin/env python3

# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: armory
# DEPS: json,pathlib
# ROLE: Parse scattered key txt files, merge into ~/.concierge/vault.json
# AUTHOR: Bleak
# SESSION: 2026-09-11
# TIER: 3
# [/DNA_TAG]

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
import shutil
import sys
from datetime import datetime
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
    # Puter auth tokens (JWT). Without this rule they classify as None and are
    # dropped on the floor; the operator mints these regularly from the web UI.
    (r"eyJ[A-Za-z0-9_\-\.]{40,}", "puter", "PUTER_AUTH_TOKEN", "dickshits"),
    (r"AQ\.Ab8RN6I", "jules", "JULES_API_KEY", "mo_keys"),
    (r"AQ\.Ab8RN6J", "jules", "JULES_API_KEY", "mo_keys"),
    (r"AQ\.Ab8RN6K", "jules", "JULES_API_KEY", "mo_keys"),
    (r"AQ\.Ab8RN6L", "jules", "JULES_API_KEY", "mo_keys"),
    (r"AIzaSy", "gemini", "GEMINI_API_KEY", "mo_keys"),
    (r"oxp", "sageox", "SAGEOX_API_KEY", "mo_mo_mo_keys"),
    # Mistral Console keys: bare 32-char alphanumeric (no prefix) — catch last, most restrictive
    # Vibestral plan also uses same format; route both to mistral provider
    (r"[A-Za-z0-9]{32}", "mistral", "MISTRAL_API_KEY", "vibestral"),
]

# Files to scan (in priority order)
SCAN_FILES = [
    # Known root-level variants; keep misspellings here so a future drop-in
    # file is ingested automatically rather than silently missed.
    Path.home() / "moo_mo_keys.txt",
    Path.home() / "mo_mo_mo_krys.txt",
    Path.home() / "mo_mo_mo_keys.txt",
    Path.home() / "mo_mo_keys.txt",
    Path.home() / "mo_keys.txt",
    Path.home() / "mo-keys.txt",
    # The operator's live drop-box. Keys land here by habit, so it has to be on
    # the scan list or they sit unread (Groq x5 / Cerebras x4 / Puter x3
    # dropped 2026-09-15 were invisible to every scanner until this line).
    Path.home() / "dickshits.txt",
    Path.home() / "The-Werkz" / "Official-Vertical-AI-Boardroom" / "mo_keys.txt",
    Path.home() / "opernrouter_api_vault_valet_concierge.txt",
    Path.home() / "vibestral.md",
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
        # bare 32-char mistral keys must be exact length
        if provider == "mistral" and pattern == r"[A-Za-z0-9]{32}":
            if len(token) == 32 and re.fullmatch(pattern, token):
                return (provider, env_var, source)
            continue
        if re.match(pattern, token):
            return (provider, env_var, source)
    return None


def extract_keys_from_file(path: Path) -> list:
    """Extract all API-key-like tokens from a text file."""
    if not path.exists():
        return []
    text = path.read_text(errors="ignore")
    # Match common key patterns plus explicitly labelled SageOx keys.
    patterns = [
        r"sk-[a-zA-Z0-9_\-]{20,}",
        r"gsk_[a-zA-Z0-9]{20,}",
        r"csk-[a-zA-Z0-9]{20,}",
        r"AQ\.Ab8RN6[a-zA-Z0-9_\-]{20,}",
        r"AIzaSy[a-zA-Z0-9_\-]{20,}",
        r"oxp[a-zA-Z0-9_\-]{20,}",
        # Puter auth tokens are JWTs (header.payload.signature). Without this the
        # three-part token matches nothing, so it is never extracted and the
        # PREFIX_MAP rule for it is unreachable — the key silently never lands.
        r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",
        r"\b[A-Za-z0-9]{32}\b",
    ]
    keys = []
    for pat in patterns:
        for m in re.finditer(pat, text):
            k = m.group(0).strip()
            if k not in keys:
                keys.append(k)
    for m in re.finditer(r"SAGEOX_API_KEY\s*=\s*[\"']?([A-Za-z0-9_\-]{20,})", text):
        k = m.group(1).strip()
        if k not in keys:
            keys.append(k)
    return keys


def load_vault() -> dict:
    if VAULT.exists():
        return json.loads(VAULT.read_text())
    return {"scanned_keys": 0, "live_providers": [], "keys": {}, "keys_by_provider": {}}


def save_vault(vault: dict):
    VAULT.parent.mkdir(parents=True, exist_ok=True)
    if VAULT.exists():
        backup = VAULT.with_name(
            f"vault.json.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}_ingest"
        )
        shutil.copy2(VAULT, backup)
        backup.chmod(0o600)
    vault["consolidated_at"] = datetime.now().isoformat()
    tmp = VAULT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(vault, indent=2) + "\n")
    tmp.chmod(0o600)
    tmp.replace(VAULT)
    VAULT.chmod(0o600)


def _promote(vault, existing, pools, provider, key, source, env_var, force):
    """Shared promote path: pool append + optional primary set.
    Returns (is_new, is_dup)."""
    if provider not in pools:
        pools[provider] = []
    is_new = key not in pools[provider]
    if is_new:
        pools[provider].append(key)
    # Set as primary if absent, forced, or the existing slot is dead
    # (retired/leaked/empty) — a fresh valet-captured key must revive it.
    _cur = existing.get(provider, {})
    _dead = not _cur.get("key") or _cur.get("status") in ("retired", "leaked")
    if provider not in existing or force or _dead:
        existing[provider] = {
            "source": source,
            "prefix": key[:16] + "...",
            "key": key,
            "env_var": env_var,
            "status": "live",
        }
    return is_new, not is_new


def direct_key_route(vault, args, force, dry_run):
    """--direct-key NAME VALUE  (NAME may be AUTO to infer from prefix).
    The valet interceptor (~/.bashrc) hands naked keys here. Supports the
    two bash forms:
      export GEMINI_API_KEY=AIzaSy...   -> NAME=GEMINI_API_KEY
      bash bare key token into shell    -> NAME=AUTO (provider from prefix)
    """
    if len(args) < 2:
        print("usage: vault_ingest.py --direct-key [NAME|AUTO] <KEY>")
        return None
    name = args[0]
    token = args[1].strip().strip('"').strip("'")
    result = classify_key(token)
    if not result:
        print(f"[valet] unclassified key prefix — not routed: {token[:12]}...")
        return None
    provider, env_var, source = result
    if name != "AUTO":
        env_var = name  # explicit var name wins (e.g. GEMINI_API_KEY)
    existing = vault.setdefault("keys", {})
    pools = vault.setdefault("keys_by_provider", {})
    is_new, _dup = _promote(vault, existing, pools, provider, token,
                            f"valet:{source}", env_var, force)
    if not dry_run:
        save_vault(vault)
    tag = "NEW" if is_new else "dup"
    print(f"[valet] {provider}/{env_var} key ...{token[-4:]} -> vault ({tag})")
    return vault


def main():
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    argv = sys.argv[1:]

    # --- VALET DIRECT ROUTE (no file scan): keys bashed naked into the shell ---
    if "--direct-key" in argv:
        di = argv.index("--direct-key")
        direct_key_route(load_vault(), argv[di + 1:di + 3], force, dry_run)
        return

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

            is_new, is_dup = _promote(vault, existing, pools, provider, k,
                                      f"{source}:{f.name}", env_var, force)
            if is_new:
                new_count += 1
            if is_dup:
                skip_count += 1

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
            print(f"    key_{keys.index(k) + 1}{marker} (value withheld)")

    if not dry_run:
        save_vault(vault)
        print(f"\nVault saved: {VAULT}")
    else:
        print(f"\n(dry run — vault not saved)")


if __name__ == "__main__":
    main()
