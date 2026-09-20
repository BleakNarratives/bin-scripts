#!/usr/bin/env python3

# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: armory
# DEPS: datetime,json,os,pathlib,sys,urllib
# ROLE: [DNA_TAG] (legacy: last_sync=2026-09-07)
# AUTHOR: Bleak
# SESSION: 2026-09-11
# TIER: 3
# [/DNA_TAG]

"""
[DNA_TAG]
ORIGIN: BleakNarratives/model_scout
PILLAR: cli-tools
PATH: ~/bin/model_scout.py
LAST_SYNC: 2026-09-07
[/DNA_TAG]

model_scout — the ecosystem's single call-out utility for model availability.

Pulls LIVE catalogs from every provider we hold vault keys for (OpenRouter,
Groq, Cerebras), merges a curated lane-status table (audited claims: live /
dead / paid / pending), and writes ONE canonical registry:
~/RootBase/model_registry.json

Usage:
  model_scout                 # refresh + print the one-screen availability board
  model_scout --json          # machine-readable board
  model_scout --free          # free lanes only (live catalogs + curated)

Keys come from the concierge vault (source ~/bin/vault-env or vault-launch).
A provider whose key is missing or dead degrades to an honest status entry —
never fabricated model rows.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_PATH = Path.home() / "RootBase" / "model_registry.json"
VERSION = "1.0.0"

GOLD = "\033[38;5;179m"
CREAM = "\033[38;5;223m"
CRIMSON = "\033[38;5;167m"
STEEL = "\033[38;5;110m"
DIM = "\033[2m"
RESET = "\033[0m"

# ─── live catalog pullers ───────────────────────────────────

def _get(url, token=None):
    headers = {"User-Agent": "model_scout/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def pull_openrouter():
    """Public endpoint — no key needed for the catalog itself."""
    data = _get("https://openrouter.ai/api/v1/models")
    free, paid = [], []
    for m in data.get("data", []):
        p = m.get("pricing", {})
        row = {
            "id": m.get("id", ""),
            "ctx": m.get("context_length") or 0,
            "prompt_per_m": round(float(p.get("prompt", "0") or 0) * 1_000_000, 4),
        }
        if p.get("prompt") == "0" and p.get("completion") == "0":
            free.append(row)
        elif row["prompt_per_m"] >= 0:   # -1 = pricing varies; skip sort pollution
            paid.append(row)
    free.sort(key=lambda r: -r["ctx"])
    paid.sort(key=lambda r: r["prompt_per_m"])
    return {"status": "LIVE", "free": free, "paid_top": paid[:25],
            "counts": {"free": len(free), "paid": len(paid)}}


def pull_groq():
    key = (os.environ.get("GROQ_API_KEY_POOL", os.environ.get("GROQ_API_KEY", ""))
           .split(",")[0].strip())
    if not key:
        return {"status": "NO_KEY"}
    try:
        data = _get("https://api.groq.com/openai/v1/models", key)
        models = sorted(m["id"] for m in data.get("data", []))
        return {"status": "LIVE", "models": models, "count": len(models)}
    except urllib.error.HTTPError as e:
        return {"status": f"HTTP_{e.code}"}
    except Exception as e:
        return {"status": "ERROR", "detail": str(e)[:120]}


def pull_cohere():
    key = os.environ.get("COHERE_API_KEY", "").strip()
    if not key:
        return {"status": "NO_KEY"}
    try:
        data = _get("https://api.cohere.com/v1/models", key)
        models = sorted(m.get("name", m.get("id", "")) for m in data.get("models", []))
        return {"status": "LIVE", "models": models, "count": len(models)}
    except urllib.error.HTTPError as e:
        return {"status": f"HTTP_{e.code}"}
    except Exception as e:
        return {"status": "ERROR", "detail": str(e)[:120]}


def pull_zai():
    key = os.environ.get("ZAI_API_KEY", "").strip()
    if not key:
        return {"status": "NO_KEY"}
    try:
        data = _get("https://open.bigmodel.cn/api/paas/v4/models", key)
        models = sorted(m.get("id", "") for m in data.get("data", []))
        return {"status": "LIVE", "models": models, "count": len(models)}
    except urllib.error.HTTPError as e:
        return {"status": f"HTTP_{e.code}"}
    except Exception as e:
        return {"status": "ERROR", "detail": str(e)[:120]}


def pull_cloudflare():
    token = os.environ.get("CF_API_TOKEN", "").strip()
    acct = os.environ.get("CF_ACCOUNT_ID", "").strip()
    if not token or not acct:
        return {"status": "NO_KEY", "detail": "need CF_API_TOKEN + CF_ACCOUNT_ID"}
    try:
        data = _get(f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/models/search", token)
        models = sorted(m.get("id", "") for m in data.get("result", []))[:40]
        return {"status": "LIVE", "models": models, "count": len(models)}
    except urllib.error.HTTPError as e:
        return {"status": f"HTTP_{e.code}"}
    except Exception as e:
        return {"status": "ERROR", "detail": str(e)[:120]}


def pull_mistral():
    key = os.environ.get("MISTRAL_API_KEY", os.environ.get("VIBE_API_KEY", "")).strip()
    if not key:
        return {"status": "NO_KEY"}
    try:
        data = _get("https://api.mistral.ai/v1/models", key)
        models = sorted(m["id"] for m in data.get("data", []))
        return {"status": "LIVE", "models": models, "count": len(models)}
    except urllib.error.HTTPError as e:
        return {"status": f"HTTP_{e.code}"}
    except Exception as e:
        return {"status": "ERROR", "detail": str(e)[:120]}


def pull_cerebras():
    key = (os.environ.get("CEREBRAS_API_KEY_POOL", os.environ.get("CEREBRAS_API_KEY", ""))
           .split(",")[0].strip())
    if not key:
        return {"status": "NO_KEY"}
    try:
        data = _get("https://api.cerebras.ai/v1/models", key)
        models = sorted(m["id"] for m in data.get("data", []))
        return {"status": "LIVE", "models": models, "count": len(models)}
    except urllib.error.HTTPError as e:
        return {"status": f"HTTP_{e.code}"}
    except Exception as e:
        return {"status": "ERROR", "detail": str(e)[:120]}


# ─── curated lane table (audited 2026-09-07, ledger seq 13-15) ──

# Source of truth for tier data: mnfst/awesome-free-llm-apis (maintained,
# community-audited) + our own live probes. Provenance recorded per lane.
CURATED_LANES = [
    {"lane": "openrouter-free", "status": "LIVE",
     "note": "verified 200 on creditless vault keys (minimax-m3, nemotron-3-super-120b, "
             "nemotron-3-ultra-550b 1M ctx). Per-key DAILY cap: 429 free-models-per-day; "
             "$10 credits -> 1000 free req/day. Rotation spreads, does not remove."},
    {"lane": "openrouter-hy4", "status": "PAID",
     "note": "tencent/hy4-preview live, 1M ctx, ~$0.834/M prompt tok; all 5 vault keys 402. "
             "Unlock: buy ~$5 credits. Wrapper: ~/bin/hy4"},
    {"lane": "github-models", "status": "DEAD",
     "note": "HTTP 410 github_models_retirement_brownout on all models (audited 2026-09-07). "
             "Do not build on it."},
    {"lane": "gemini-ai-studio", "status": "PENDING_KEY",
     "note": "wire exists: fresh AIzaSy key -> ~/mo_keys.txt -> vault_ingest.py --force -> "
             "vault-env exports GEMINI_API_KEY. Verified tier data (mnfst list): Gemini "
             "Flash 1M ctx 1,500 RPD free; 2.5 Pro 5 RPM/50 RPD. No card. Free-tier "
             "prompts may train Google products."},
    {"lane": "mistral-free-mode", "status": "BLOCKED_402",
     "note": "vault key held (keys.mistral from vibe wiring) but live probe 2026-09-07 "
             "returned 402 — account not on free mode. Free mode is default-on with "
             "$10/mo credits for NEW accounts; operator: check console.mistral.ai "
             "plan. Free-tier prompts may train Mistral unless opted out."},
    {"lane": "cohere-trial", "status": "NO_KEY",
     "note": "free trial key, no card: 1,000 calls/mo, non-commercial. Command A+ 218B "
             "128K, Command A 256K, Aya Vision. Operator: dashboard.cohere.com/api-keys; "
             "drop key into vault as keys_by_provider.cohere (model_scout reads COHERE_API_KEY)."},
    {"lane": "cloudflare-workers-ai", "status": "NO_KEY",
     "note": "10,000 Neurons/day free, no card, 75+ models incl. llama-4-scout 131K. "
             "Needs token + account id: vault keys_by_provider.cloudflare[0]=token and "
             "CF_ACCOUNT_ID (model_scout reads CF_API_TOKEN + CF_ACCOUNT_ID). "
             "Operator: dash.cloudflare.com."},
    {"lane": "zai-glm-flash", "status": "NO_KEY",
     "note": "Zhipu GLM-4.7-Flash permanent free, 200K ctx, no card. Auto-ingest wired: "
             "drop an 'id.*' key into ~/mo_keys.txt -> vault_ingest.py promotes to zai "
             "-> model_scout reads ZAI_API_KEY. Operator: open.bigmodel.cn."},
    {"lane": "aihubmix", "status": "UNVERIFIED",
     "note": "listicle claim only (Kimi K3 / GLM-5 / Gemini 3, 'free'). Never hit the "
             "endpoint. Needs operator signup before we treat it as real."},
    {"lane": "chatgpt-free-proxy-tools", "status": "REFUSED",
     "note": "any tool that 'tricks' a backend into auth-bypass (Freebuff2API-style) is "
             "off the table. Verdict logged ledger seq 15."},
]


# ─── board ──────────────────────────────────────────────────

def build_registry():
    reg = {
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "providers": {
            "openrouter": pull_openrouter(),
            "groq": pull_groq(),
            "cerebras": pull_cerebras(),
            "mistral": pull_mistral(),
            "cohere": pull_cohere(),
            "zai": pull_zai(),
            "cloudflare": pull_cloudflare(),
        },
        "curated_lanes": CURATED_LANES,
        "tools": {
            "fox": "~/bin/fox — OpenRouter chat TUI (free catalog default minimax-m3:free)",
            "hy4": "~/bin/hy4 — Hunyuan Hy4 one-shot (paid lane)",
            "ox": "~/bin/ox — groq/openrouter one-shot CLI",
        },
    }
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(reg, indent=2))
    return reg


def print_board(reg, free_only=False):
    p = reg["providers"]
    print(f"{GOLD}{p['openrouter']['counts']['free']} free OpenRouter models"
          f"{DIM} (+{p['openrouter']['counts']['paid']} paid) | "
          f"groq: {p['groq'].get('count', p['groq']['status'])} models | "
          f"cerebras: {p['cerebras'].get('count', p['cerebras']['status'])} models | "
          f"mistral: {p['mistral'].get('count', p['mistral']['status'])} | "
          f"cohere: {p['cohere'].get('count', p['cohere']['status'])} | "
          f"zai: {p['zai'].get('count', p['zai']['status'])} | "
          f"cloudflare: {p['cloudflare'].get('count', p['cloudflare']['status'])}{RESET}")
    print(DIM + f"registry: {REGISTRY_PATH} · {reg['generated_at'][:16]}Z" + RESET)
    if not free_only:
        top = p["openrouter"]["paid_top"][:5]
        if top:
            print(f"\n{STEEL}cheapest paid lanes ($/M prompt tok):{RESET}")
            for m in top:
                print(f"  {CREAM}{m['id']:<46}{DIM} ctx {m['ctx']:>9,}  ${m['prompt_per_m']:.4f}/M{RESET}")
    print(f"\n{GOLD}top free lanes (ctx):{RESET}")
    for m in p["openrouter"]["free"][:10]:
        print(f"  {CREAM}{m['id']:<46}{DIM} ctx {m['ctx']:>9,}{RESET}")
    if p["groq"].get("models"):
        print(f"\n{GOLD}groq models:{RESET}")
        print(f"  {DIM}{', '.join(p['groq']['models'][:8])}{RESET}")
    if p["cerebras"].get("models"):
        print(f"\n{GOLD}cerebras models:{RESET}")
        print(f"  {DIM}{', '.join(p['cerebras']['models'][:8])}{RESET}")
    if p["mistral"].get("models"):
        print(f"\n{GOLD}mistral models (FREE MODE — $10/mo credits, no card):{RESET}")
        print(f"  {DIM}{', '.join(p['mistral']['models'][:10])}{RESET}")
    print(f"\n{GOLD}curated lanes:{RESET}")
    for lane in reg["curated_lanes"]:
        status = lane["status"]
        color = {"LIVE": STEEL, "DEAD": CRIMSON, "PAID": CREAM,
                 "PENDING_KEY": CREAM, "UNVERIFIED": DIM, "REFUSED": CRIMSON}.get(status, DIM)
        print(f"  {color}{status:<12}{RESET} {lane['lane']}")
        print(f"  {DIM}{' ' * 13}{lane['note'][:110]}{RESET}")


def main():
    args = sys.argv[1:]
    reg = build_registry()
    if "--json" in args:
        print(json.dumps(reg, indent=2))
        return
    print_board(reg, free_only="--free" in args)


if __name__ == "__main__":
    main()
