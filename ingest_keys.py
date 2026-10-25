#!/usr/bin/env python3
"""ingest_keys.py -- read keys from a text file into the concierge vault.

Why this is not a regex sweep: the previous version searched the whole file for
any 30+ character token and wrote the first hit. It could attach a DeepSeek key
to Cohere, and it wrote `vault["keys"]["COHERE_API_KEY"] = "..."` -- a schema
nothing reads, because the vault stores `keys.<provider>.key`.

Rules here:

  1. ANCHORED. A key is assigned only when a provider is anchored to it: an
     `NAME=key` / `NAME: key` line, a provider word shortly before the key, or
     an unambiguous prefix (gsk_ -> groq, hf_ -> huggingface, AIza -> gemini).
  2. AMBIGUOUS IS NOT GUESSED. A bare sk-... key is OpenAI, DeepSeek, Stability
     or SambaNova depending on which one it is, so it is reported as UNASSIGNED
     for a human to place. Guessing is how a key ends up on the wrong provider.
  3. NO WRITE WITHOUT --write. Default is a dry run that prints what it would do.
  4. VALIDATE BEFORE WRITE when a provider has a probe. Unprobed providers are
     written as status=unverified, never as live.
  5. Keys are never printed. Only a masked preview and a fingerprint.

Usage:
  python3 bin/ingest_keys.py [--file ~/dickshits.txt] [--write] [--no-verify]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

VAULT = Path.home() / ".concierge" / "vault.json"
DEFAULT_SOURCE = Path.home() / "dickshits.txt"

# Shape evidence. A key matching exactly one provider's pattern can be placed by
# shape alone; a pattern shared by several providers cannot.
SHAPES = {
    "groq": [r"gsk_[A-Za-z0-9]{20,}"],
    "huggingface": [r"hf_[A-Za-z0-9]{20,}"],
    "gemini": [r"AIza[0-9A-Za-z_\-]{30,}"],
    "cerebras": [r"csk-[A-Za-z0-9]{20,}"],
    "elevenlabs": [r"sk_[a-f0-9]{48}"],
    "cohere": [r"[A-Za-z0-9]{40}"],
    "openai": [r"sk-proj-[A-Za-z0-9_\-]{20,}", r"sk-[A-Za-z0-9]{20,}"],
    "deepseek": [r"sk-[a-f0-9]{32}"],
    "stability": [r"sk-[A-Za-z0-9]{32,}"],
    "sambanova": [r"[a-f0-9]{36}"],
    "mistral": [r"[A-Za-z0-9]{32}"],
    "openrouter": [r"sk-or-v1-[A-Za-z0-9]{32,}"],
    "anthropic": [r"sk-ant-[A-Za-z0-9_\-]{20,}"],
    "github": [r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"],
    "zai": [r"[A-Za-z0-9]{32}\.[A-Za-z0-9]{16}"],
}

# Words that may name a provider in prose or as a label.
ALIASES = {
    "groq": "groq", "llama groq": "groq",
    "huggingface": "huggingface", "hugging face": "huggingface", "hf": "huggingface",
    "gemini": "gemini", "google ai studio": "gemini", "ai studio": "gemini",
    "cerebras": "cerebras", "cohere": "cohere", "elevenlabs": "elevenlabs",
    "openai": "openai", "deepseek": "deepseek", "stability": "stability",
    "sambanova": "sambanova", "mistral": "mistral", "openrouter": "openrouter",
    "anthropic": "anthropic", "claude": "anthropic", "github": "github",
    "zhipu": "zai", "zai": "zai", "glm": "zai",
}

ENV_LABELS = {
    "GROQ_API_KEY": "groq", "GEMINI_API_KEY": "gemini",
    "GOOGLE_API_KEY": "gemini", "HF_TOKEN": "huggingface",
    "HUGGINGFACE_API_KEY": "huggingface", "CEREBRAS_API_KEY": "cerebras",
    "COHERE_API_KEY": "cohere", "DEEPSEEK_API_KEY": "deepseek",
    "OPENAI_API_KEY": "openai", "MISTRAL_API_KEY": "mistral",
    "OPENROUTER_API_KEY": "openrouter", "STABILITY_API_KEY": "stability",
    "SAMBANOVA_API_KEY": "sambanova", "ELEVENLABS_API_KEY": "elevenlabs",
    "ANTHROPIC_API_KEY": "anthropic", "GITHUB_TOKEN": "github",
    "ZAI_API_KEY": "zai",
}

ANCHOR_WINDOW = 60  # characters of context checked before a key
TOKEN = re.compile(r"[A-Za-z0-9_\-]{20,}")

# Probe targets. Value is (url, auth_style) or (url, auth_style, method, body).
#
# Every entry here was checked against the live endpoint before being trusted:
# an unauthenticated request must produce 401/403 ("this route needs a key"),
# and a deliberately bogus key must also produce 401/403 ("this route rejects
# the wrong key"). A route that answers 200 to a bogus key cannot validate
# anything -- it is listed in NO_PROBE instead of being used to declare a key
# live, which would be a confident lie.
PROBES = {
    "groq": ("https://api.groq.com/openai/v1/models", "bearer"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/models", "goog"),
    "huggingface": ("https://huggingface.co/api/whoami-v2", "bearer"),
    "openrouter": ("https://openrouter.ai/api/v1/key", "bearer"),
    "mistral": ("https://api.mistral.ai/v1/models", "bearer"),
    "deepseek": ("https://api.deepseek.com/models", "bearer"),
    "cerebras": ("https://api.cerebras.ai/v1/models", "bearer"),
    "cohere": ("https://api.cohere.com/v1/models", "bearer"),
    "anthropic": ("https://api.anthropic.com/v1/models", "anthropic"),
    "github": ("https://api.github.com/user", "bearer"),
    # ── added 2026-10-25, each validated as described above ──
    "openai": ("https://api.openai.com/v1/models", "bearer"),
    "orcarouter": ("https://api.orcarouter.ai/v1/models", "bearer"),
    "elevenlabs": ("https://api.elevenlabs.io/v1/user", "xi"),
    "stability": ("https://api.stability.ai/v1/user/account", "bearer"),
    "jules": ("https://jules.googleapis.com/v1alpha/sessions", "goog"),
    "swarms": ("https://api.swarms.world/v1/models", "bearer"),
    # Empty JSON body on purpose: this validates the key and then fails body
    # validation (400/422), so it never generates a billed completion.
    "aimlapi": ("https://api.aimlapi.com/v1/chat/completions", "bearer",
                "POST", "{}"),
}

# Providers with a vault entry but no trustworthy probe. Kept explicit so the
# absence of a verdict is visible rather than silently reading as "fine".
NO_PROBE = {
    "sambanova": ("models list is public and ignores the key; chat endpoint "
                  "validates the body before auth, so a bad key answers 400"),
    "bazaar": "no resolvable API host from this machine",
    "sageox": "no resolvable API host from this machine",
    "vibe": "no resolvable API host from this machine",
    "puter": "no documented key-authenticated REST endpoint; auth is browser-side",
    "zenmux": ("returns 403 for a bogus key as well as a real one, so the "
               "status code cannot distinguish a good key from a bad one"),
    "google": "retired entry, holds no key",
    "unknown": "placeholder entry, no provider identity",
}


def mask(key: str) -> str:
    """Preview that identifies a key without revealing it."""
    if len(key) <= 10:
        return "*" * len(key)
    fp = hashlib.sha256(key.encode()).hexdigest()[:8]
    return f"{key[:4]}...{key[-3:]} [{fp}]"


def provider_for_shape(token: str) -> tuple[str | None, int]:
    """(provider, how_many_providers_match). 1 means shape alone is decisive."""
    hits = [name for name, pats in SHAPES.items()
            if any(re.fullmatch(p, token) for p in pats)]
    return (hits[0] if len(hits) == 1 else None), len(hits)


def provider_from_context(line: str, token: str) -> str | None:
    """Nearest provider word before the key on this line, within a window."""
    pos = line.find(token)
    if pos < 0:
        return None
    window = line[max(0, pos - ANCHOR_WINDOW):pos].lower()
    best, best_at = None, -1
    for word, name in ALIASES.items():
        at = window.rfind(word)
        if at > best_at:
            best, best_at = name, at
    return best


def provider_from_label(line: str) -> str | None:
    """`GROQ_API_KEY=...` or `groq: ...` at the start of the line."""
    head = line.split("=", 1)[0].split(":", 1)[0].strip().upper()
    head = head.replace("-", "_").replace(" ", "_")
    if head in ENV_LABELS:
        return ENV_LABELS[head]
    return ALIASES.get(head.lower())


def parse_assignments(text: str) -> tuple[dict[str, list[str]], list[str], list[dict]]:
    """Split a blob into {provider: [keys]}, UNASSIGNED, and CONFLICTS. Pure.

    A unique shape beats a label. If they disagree (a `gsk_` key filed under
    GEMINI) the key is neither accepted nor guessed: it is reported, because one
    of the two is wrong and only a human knows which.
    """
    found: dict[str, list[str]] = {}
    unassigned: list[str] = []
    conflicts: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        label_hit = provider_from_label(line)
        for token in TOKEN.findall(line):
            by_shape, matches = provider_for_shape(token)
            shape_hit = by_shape if matches == 1 else None
            if shape_hit and label_hit and shape_hit != label_hit:
                if not any(c["token"] == token for c in conflicts):
                    conflicts.append({"token": token, "label": label_hit,
                                      "shape": shape_hit})
                continue
            provider = shape_hit or label_hit or provider_from_context(line, token)
            if provider:
                bucket = found.setdefault(provider, [])
                if token not in bucket:
                    bucket.append(token)
            elif token not in unassigned:
                unassigned.append(token)
    return found, unassigned, conflicts


def merge_pool(existing: str, new_keys: list[str]) -> str:
    """Append new keys to an existing comma-separated pool, de-duplicated."""
    pool = [k.strip() for k in (existing or "").split(",") if k.strip()]
    for key in new_keys:
        if key not in pool:
            pool.append(key)
    return ",".join(pool)


def probe(provider: str, key: str, timeout: int = 15) -> str:
    """Return 'live', 'HTTP_<code>', or 'no_probe'. Never echoes the key.

    'live' means the endpoint accepted the key -- 2xx, or a 400/422 that can
    only be reached after authentication. 401/403 means the key was rejected.
    """
    target = PROBES.get(provider)
    if not target:
        return "no_probe"
    url, style = target[0], target[1]
    method = target[2] if len(target) > 2 else "GET"
    body = target[3].encode() if len(target) > 3 and target[3] else None
    headers = {"User-Agent": "ingest_keys/2.0"}
    if style == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif style == "goog":
        headers["x-goog-api-key"] = key
    elif style == "xi":
        headers["xi-api-key"] = key
    elif style == "anthropic":
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return "live"
    except urllib.error.HTTPError as e:
        # An authenticated-but-malformed POST is proof the key was accepted.
        # Restricted to POST: on a GET, a 400 says nothing about auth.
        if e.code in (400, 422) and method == "POST":
            return "live"
        return f"HTTP_{e.code}"
    except (urllib.error.URLError, OSError):
        return "unreachable"


def load_vault(path: Path) -> dict:
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        blob = {}
    blob.setdefault("keys", {})
    return blob


def apply_keys(blob: dict, found: dict[str, list[str]], results: dict,
               today: str) -> list[str]:
    """Write into the vault structure `keys.<provider>.{key,status,added}`. Pure."""
    changes = []
    for provider, keys in found.items():
        entry = blob["keys"].get(provider)
        if not isinstance(entry, dict):
            entry = {}
        before = entry.get("key", "")
        entry["key"] = merge_pool(before, keys)
        status = results.get(provider, "unverified")
        entry["status"] = "live" if status == "live" else "unverified"
        entry["added"] = entry.get("added") or today
        entry["source"] = "ingest_keys"
        blob["keys"][provider] = entry
        changes.append(f"{provider}: +{len(keys)} key(s), status={entry['status']}")
    return changes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ingest API keys into the vault.")
    ap.add_argument("--file", default=str(DEFAULT_SOURCE))
    ap.add_argument("--vault", default=str(VAULT))
    ap.add_argument("--write", action="store_true", help="actually update the vault")
    ap.add_argument("--no-verify", action="store_true", help="skip live probes")
    a = ap.parse_args(argv)

    src = Path(a.file).expanduser()
    if not src.is_file():
        print(f"ingest_keys: no such file: {src}", file=sys.stderr)
        return 1
    text = src.read_text(encoding="utf-8", errors="replace")

    found, unassigned, conflicts = parse_assignments(text)
    if not found and not unassigned and not conflicts:
        print("ingest_keys: no key-shaped tokens found")
        return 1

    results = {}
    for provider, keys in sorted(found.items()):
        if a.no_verify:
            results[provider] = "unverified"
            continue
        results[provider] = probe(provider, keys[0])
        print(f"{provider:14} {len(keys)} key(s)  probe={results[provider]}"
              f"  first={mask(keys[0])}")

    if conflicts:
        print("\nCONFLICTS (label and key shape disagree; fix the label by hand):")
        for c in conflicts:
            print(f"  {mask(c['token'])}  label={c['label']} but shape={c['shape']}")

    if unassigned:
        print("\nUNASSIGNED (place these by hand; guessing is how keys land wrong):")
        for token in unassigned:
            in_vault = any(token in str((v or {}).get("key", ""))
                           for v in load_vault(Path(a.vault).expanduser())["keys"].values()
                           if isinstance(v, dict))
            print(f"  {mask(token)}  matches {provider_for_shape(token)[1]} shape(s)"
                  f"{'  (already in vault)' if in_vault else ''}")

    blob = load_vault(Path(a.vault).expanduser())
    changes = apply_keys(blob, found, results, date.today().isoformat())

    if not a.write:
        print("\nDRY RUN -- nothing written. Planned changes:")
        for line in changes or ["(none)"]:
            print(f"  {line}")
        print("\nRe-run with --write to apply.")
        return 0

    if not changes:
        print("\nNothing to write.")
        return 0
    target = Path(a.vault).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(blob, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    print(f"\nWrote {len(changes)} provider(s) to {target}:")
    for line in changes:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    import telemetry
    telemetry.install("ingest_keys", sys.argv[1:])
    sys.exit(main())
