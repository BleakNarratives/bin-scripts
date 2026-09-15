#!/usr/bin/env python3

# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: armory
# DEPS: stdlib only
# ROLE: Local OpenAI-compatible gateway with vault key-pool rotation
# AUTHOR: Bleak
# SESSION: 2026-09-15
# TIER: 3
# [/DNA_TAG]

"""
ai_gateway — one local OpenAI-compatible endpoint that rotates your vault key pools.

WHY THIS EXISTS
  aider and vibe each hold ONE key per provider (`api_key_env_var` in
  .vibe/config.toml, GROQ_API_KEY for aider). When that key 429s the run dies
  and the pool sitting in the vault is never touched.

  The rotation logic already exists — The-Werkz/Official-Vertical-AI-Boardroom/
  core/router.py has `_groq_next_key`, a per-key 429 cooldown, and key
  quarantine. It is a Python module, and neither CLI can call a Python module.
  This exposes it as the only thing they *can* call: an HTTP endpoint.

POINT THINGS AT IT
  Path routing - no separator to get wrong, and any model id survives:
    export OPENAI_API_BASE=http://127.0.0.1:7071/v1/auto   # fallback chain
    export OPENAI_API_BASE=http://127.0.0.1:7071/v1/groq   # pin one provider
    export OPENAI_API_KEY=local-gateway
    aider --model openai/default

MODEL ROUTING
  Provider comes from the URL path (/v1/<provider>/chat/completions) or from a
  "<provider>::<model>" prefix. A bare "/" is deliberately NOT a separator:
  Groq and OpenRouter both serve ids like "openai/gpt-oss-120b", so a leading
  "openai/" cannot be told apart from the OpenAI provider and would silently
  misroute every Groq call.

  "auto" (path /v1/auto, or prefix auto::) walks AUTO_CHAIN and falls through
  to the next provider on 429/5xx/auth failure.

  Stdlib only. No writes except the log. Key values are never printed.
  Run: python3 ~/bin/ai_gateway.py [--port 7071]
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VAULT_JSON = Path.home() / ".concierge" / "vault.json"
LOG_FILE = Path.home() / "ai_gateway.log"
DEFAULT_PORT = 7071
DEFAULT_PROVIDER = "groq"

# OpenAI-compatible chat endpoints. Gemini exposes an OpenAI-compatible surface
# at the /v1beta/openai path; providers absent from this map are skipped rather
# than guessed at.
PROVIDERS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "cerebras": "https://api.cerebras.ai/v1/chat/completions",
    "mistral": "https://api.mistral.ai/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
}

# Order "auto" walks. Cheapest/free tiers first.
AUTO_CHAIN = ("groq", "cerebras", "openrouter", "mistral", "gemini", "openai")

FALLBACK_MODEL = {
    "groq": "openai/gpt-oss-120b",
    "cerebras": "llama-3.3-70b",
    "openrouter": "z-ai/glm-5.2:free",
    "mistral": "mistral-small-latest",
    # Verified against the live API 2026-09-15: gemini-2.5-flash and -lite are
    # refused for this key ("no longer available to new users"), and
    # gemini-3.5-flash accepts the request then never answers (HTTP 000 at 90s
    # — the "just sits there thinking" hang). The -latest alias answered in
    # ~2.7s and tracks whatever Google currently serves, so prefer it.
    "gemini": "gemini-flash-latest",
    "openai": "gpt-4o-mini",
}

COOLDOWN_429 = 30.0      # per-key cooldown after a rate limit
QUARANTINE_401 = 3600.0  # dead auth: park the key for an hour
MAX_ATTEMPTS = 12        # across the whole pool, not per key
# When every key in a pool has failed auth, the provider is not a routing
# candidate at all. Without this, a pool of revoked keys stays first in the
# chain and every request spends its whole attempt budget before reaching a
# provider that works.
PROVIDER_DEAD_TTL = 300.0

_lock = threading.Lock()
_key_state: dict[str, dict] = {}   # "provider:KEYTAIL" -> {"cooldown_until": float, "kind": str}
_rotation: dict[str, int] = {}     # provider -> round-robin cursor
_provider_dead: dict[str, float] = {}  # provider -> cooldown_until when pool is auth-dead


def log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except OSError:
        pass


# ── key pools ────────────────────────────────────────────────────────────────

def _pools_from_vault() -> dict[str, list[str]]:
    """Read keys_by_provider from the concierge vault. Names, never values."""
    if not VAULT_JSON.exists():
        return {}
    try:
        data = json.loads(VAULT_JSON.read_text())
    except (OSError, ValueError):
        return {}
    out: dict[str, list[str]] = {}
    pools = data.get("keys_by_provider", {})
    if isinstance(pools, dict):
        for provider, keys in pools.items():
            if isinstance(keys, list):
                good = [k for k in keys
                        if isinstance(k, str) and k and "PLACEHOLDER" not in k]
                if good:
                    out[provider] = good
    # Single-key entries fill gaps for providers with no pool.
    keys = data.get("keys", {})
    if isinstance(keys, dict):
        for provider, entry in keys.items():
            if provider in out:
                continue
            value = entry.get("key") if isinstance(entry, dict) else entry
            if isinstance(value, str) and value and "PLACEHOLDER" not in value:
                out[provider] = [value]
    return out


def pool_for(provider: str) -> list[str]:
    """Env POOL first (what vault-env exports), then the vault file."""
    env_pool = os.environ.get(f"{provider.upper()}_API_KEY_POOL", "")
    keys = [k.strip() for k in env_pool.split(",") if k.strip()]
    if keys:
        return keys
    single = os.environ.get(f"{provider.upper()}_API_KEY", "")
    if single:
        return [single]
    return _pools_from_vault().get(provider, [])


def _tag(key: str) -> str:
    """Identify a key for logs/state without revealing it."""
    return f"{key[:4]}...{key[-4:]}" if len(key) > 10 else "short"


def next_keys(provider: str) -> list[str]:
    """Pool ordered from the round-robin cursor, skipping cooled-down keys.

    If every key is cooling down, returns the full pool anyway — a slept-on key
    beats a hard failure.
    """
    pool = pool_for(provider)
    if not pool:
        return []
    with _lock:
        start = _rotation.get(provider, 0) % len(pool)
        _rotation[provider] = (start + 1) % len(pool)
    ordered = pool[start:] + pool[:start]
    now = time.time()
    live = [k for k in ordered
            if _key_state.get(f"{provider}:{_tag(k)}", {}).get("cooldown_until", 0) <= now]
    return live or ordered


def _cool(provider: str, key: str, seconds: float, why: str, kind: str = "rate") -> None:
    with _lock:
        _key_state[f"{provider}:{_tag(key)}"] = {
            "cooldown_until": time.time() + seconds,
            "kind": kind,
        }
    log(f"COOL  {provider} {_tag(key)} {why} for {int(seconds)}s")


def _provider_dead_until(provider: str) -> float:
    return _provider_dead.get(provider, 0.0)


def _mark_dead_if_all_auth(provider: str) -> None:
    """Park a provider while every one of its keys is auth-failed.

    A 429 is transient and worth retrying later; a 401/403 on the whole pool
    means the keys themselves are dead, so routing to that provider is pure
    waste until someone replaces them.
    """
    pool = pool_for(provider)
    if not pool:
        return
    now = time.time()
    for key in pool:
        state = _key_state.get(f"{provider}:{_tag(key)}", {})
        if state.get("kind") != "auth" or state.get("cooldown_until", 0) <= now:
            return
    with _lock:
        _provider_dead[provider] = now + PROVIDER_DEAD_TTL
    log(f"DEAD  {provider} all {len(pool)} keys auth-failed; skipping for {int(PROVIDER_DEAD_TTL)}s")


# ── upstream call ────────────────────────────────────────────────────────────

def call_upstream(provider: str, model: str, body: dict, timeout: int = 60):
    """POST to one provider, rotating keys. Returns (status, payload_dict)."""
    url = PROVIDERS.get(provider)
    if not url:
        return 400, {"error": {"message": f"unknown provider '{provider}'"}}

    if _provider_dead_until(provider) > time.time():
        return 503, {"error": {"message":
                     f"'{provider}' skipped: every key failed auth recently "
                     f"(pool is dead, not rate-limited). Replace the keys."}}

    keys = next_keys(provider)
    if not keys:
        return 503, {"error": {"message":
                     f"no key for '{provider}' — is the vault loaded? "
                     f"try: source ~/bin/vault-env"}}

    payload = {k: v for k, v in body.items() if k != "model"}
    payload["model"] = model
    data = json.dumps(payload).encode()

    last = (502, {"error": {"message": "no attempt made"}})
    for attempt in range(min(MAX_ATTEMPTS, len(keys) * 3)):
        key = keys[attempt % len(keys)]
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {key}")
        if provider == "openrouter":
            req.add_header("HTTP-Referer", "http://localhost")
            req.add_header("X-Title", "ai_gateway")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                # Read enough to spot a quota-exhaustion body, not just a
                # status code. 1200 chars, per provider error envelope size.
                detail = e.read().decode()[:1200]
            except Exception:
                pass
            last = (e.code, {"error": {"message": detail or str(e)}})
            if e.code == 429:
                # Distinguish a real rate limit from an exhausted account. An
                # out-of-credit key answers 429 forever, so treating it as
                # transient walks the whole chain into it on every request.
                if "insufficient_quota" in detail or "credit_balance_exhausted" in detail \
                        or "exceeded your current quota" in detail:
                    _cool(provider, key, QUARANTINE_401, "quota", kind="auth")
                else:
                    _cool(provider, key, COOLDOWN_429, "429")
                continue
            # 402 = payment required (e.g. Mistral with no subscription),
            # which is as permanent as dead auth for routing purposes.
            if e.code in (401, 402, 403):
                _cool(provider, key, QUARANTINE_401, str(e.code), kind="auth")
                continue
            if e.code >= 500:
                _cool(provider, key, COOLDOWN_429, str(e.code))
                continue
            return last          # 400/404/422 are our fault, don't burn the pool
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = (502, {"error": {"message": f"transport: {e}"}})
            _cool(provider, key, COOLDOWN_429, "transport")
            continue
    _mark_dead_if_all_auth(provider)
    return last


def route(model: str, body: dict, provider: str | None = None):
    """Pick the upstream provider and call it, with fallback for 'auto'.

    Precedence: explicit provider from the URL path, then a
    "<provider>::<model>" prefix, then DEFAULT_PROVIDER.
    """
    if "::" in model:
        head, tail = model.split("::", 1)
        if provider is None and (head == "auto" or head in PROVIDERS):
            provider, model = head, tail

    if provider == "auto":
        now = time.time()
        with_keys = [p for p in AUTO_CHAIN if pool_for(p)]
        # Prefer providers that are not auth-dead; if every one is, fall back to
        # trying them anyway rather than refusing outright.
        chain = [p for p in with_keys if _provider_dead_until(p) <= now] or with_keys
        if not chain:
            return 503, {"error": {"message": "auto: no providers with keys"}}
        last = (503, {"error": {"message": "auto: no providers with keys"}})
        for candidate in chain:
            m = (model if model and model not in ("default", "auto")
                 else FALLBACK_MODEL.get(candidate, model or "default"))
            status, payload = call_upstream(candidate, m, body)
            if status == 200:
                if candidate != chain[0]:
                    log(f"AUTO  fell through to {candidate} ({m})")
                return status, payload
            last = (status, payload)
        return last

    if provider is not None:
        if provider not in PROVIDERS:
            return 400, {"error": {"message": f"unknown provider '{provider}'"}}
        return call_upstream(provider, model, body)

    # Bare model name: use the default provider.
    return call_upstream(DEFAULT_PROVIDER, model, body)


def status() -> dict:
    now = time.time()
    report = {}
    for provider in PROVIDERS:
        pool = pool_for(provider)
        if not pool:
            continue
        cooling = sum(1 for k in pool
                      if _key_state.get(f"{provider}:{_tag(k)}", {}).get("cooldown_until", 0) > now)
        report[provider] = {"keys": len(pool), "cooling_down": cooling}
    return report


# ── HTTP ─────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, obj: dict) -> None:
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send(200, {"status": "alive", "pools": status()})
        elif self.path.startswith("/v1/models"):
            ids = []
            for provider, pool in ((p, pool_for(p)) for p in PROVIDERS):
                if pool:
                    ids.append({"id": f"{provider}/{FALLBACK_MODEL.get(provider, 'default')}",
                                "object": "model", "owned_by": provider})
            self._send(200, {"object": "list", "data": ids})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        # /v1/chat/completions  and  /v1/<provider>/chat/completions
        parts = [p for p in self.path.split("?", 1)[0].split("/") if p]
        ok = parts[:1] == ["v1"] and parts[-2:] == ["chat", "completions"]
        if not ok or len(parts) not in (3, 4):
            self._send(404, {"error": {"message":
                "only /v1[/<provider>]/chat/completions is served"}})
            return
        provider = parts[1] if len(parts) == 4 else None
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            self._send(400, {"error": {"message": "invalid JSON body"}})
            return
        model = str(body.get("model") or DEFAULT_PROVIDER)
        code, payload = route(model, body, provider)
        # Every request leaves a trace: which provider actually answered, and
        # where a fallback happened. Without this the rotation is invisible and
        # a silent misroute looks identical to a working call.
        log(f"REQ  path={self.path} provider={provider or DEFAULT_PROVIDER} "
            f"model={model} -> {code}")
        if code == 200 and "usage" not in payload:
            payload["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._send(code, payload)

    def log_message(self, *_a):
        pass  # keep stderr clean; we log to LOG_FILE


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenAI-compatible rotating key gateway")
    ap.add_argument("--port", type=int, default=int(os.environ.get("AI_GATEWAY_PORT", DEFAULT_PORT)))
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    report = status()
    if not report:
        print("[gateway] WARNING: no key pools visible. Run: source ~/bin/vault-env",
              flush=True)
    for provider, info in sorted(report.items()):
        print(f"[gateway] {provider}: {info['keys']} keys ({info['cooling_down']} cooling down)",
              flush=True)
    print(f"[gateway] listening on http://{args.host}:{args.port}/v1", flush=True)
    log(f"START port={args.port} pools={ {k: v['keys'] for k, v in report.items()} }")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
