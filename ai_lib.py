#!/usr/bin/env python3
# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: armory
# DEPS: stdlib only
# ROLE: Single model client shared by the CLI, Anchor, and errfix
# AUTHOR: Bleak
# SESSION: 2026-09-15
# TIER: 3
# [/DNA_TAG]

"""ai_lib — the ONE client every local AI tool calls.

Before this, each tool held its own provider config and its own single key:
`.vibe/config.toml`, `.aider.conf.yml`, `.oxrc`, `models.py` … so a dead key in
one place was invisible everywhere else, and nothing rotated.

The unification is deliberately small:

  one config   -> ~/.config/ai/config.toml   (gateway URL + named presets)
  one endpoint -> the rotating gateway on 127.0.0.1:7071
  one call     -> ai_lib.ask()

Nothing here reads or stores an API key. The gateway owns the vault pools and
rotates them; every caller speaks the same OpenAI-shaped request to the same
localhost port.

Stdlib only.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "ai" / "config.toml"
GATEWAY_ENSURE = Path.home() / "bin" / "gateway-ensure"
DEFAULT_BASE = "http://127.0.0.1:7071"
DEFAULT_TIMEOUT = 180


# ── config ───────────────────────────────────────────────────────────────────

def load_config(path: Path | None = None) -> dict:
    """Read the single AI config. Missing or malformed -> sane defaults."""
    path = path or CONFIG_PATH
    if not path.is_file():
        return {}
    try:
        import tomllib
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def base_url(cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else load_config()
    value = cfg.get("base_url") or DEFAULT_BASE
    return str(value).rstrip("/")


def resolve_model(name: str | None, cfg: dict | None = None) -> str:
    """Map a preset name (or a raw model) to a gateway model string.

    Accepted forms:
      "auto" / "default" / None  -> the gateway's fallback chain
      a preset in [models]       -> whatever that preset expands to
      "provider::model"          -> pinned provider (gateway path routing)
      anything else              -> passed through as a bare model on the chain
    """
    cfg = cfg if cfg is not None else load_config()
    if not name:
        name = str(cfg.get("default_model") or "auto")
    presets = cfg.get("models", {})
    if isinstance(presets, dict) and name in presets:
        return str(presets[name])
    return name


# ── gateway liveness ─────────────────────────────────────────────────────────

def gateway_alive(base: str | None = None, timeout: float = 2.0) -> bool:
    url = f"{base or base_url()}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_gateway() -> bool:
    """Start the gateway on demand. Non-fatal: False just means no rotation."""
    if gateway_alive():
        return True
    if GATEWAY_ENSURE.is_file():
        try:
            subprocess.run([str(GATEWAY_ENSURE)], timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    return gateway_alive()


# ── the one call ─────────────────────────────────────────────────────────────

def _endpoint(model: str) -> tuple[str, str]:
    """(url, body_model) for a resolved model string."""
    base = base_url()
    if "::" in model:
        provider, tail = model.split("::", 1)
        return f"{base}/v1/{provider}/chat/completions", tail
    if model in ("", "auto", "default"):
        return f"{base}/v1/auto/chat/completions", "default"
    # A bare model id rides the fallback chain.
    return f"{base}/v1/auto/chat/completions", model


def _post(url: str, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    # The gateway ignores this value; it exists so OpenAI-shaped clients are happy.
    req.add_header("Authorization", "Bearer local-gateway")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:500]
        except Exception:
            pass
        raise RuntimeError(f"gateway HTTP {exc.code}: {body}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(
            f"gateway unreachable at {url} ({exc}). Start it with: bin/gateway-ensure"
        ) from None


def ask(prompt: str, model: str | None = None, system: str | None = None,
        history: list[dict] | None = None, timeout: int = DEFAULT_TIMEOUT,
        ensure: bool = True) -> str:
    """Send one prompt through the rotating gateway and return the text.

    This is the single call the whole toolchain should use. `history` is a list
    of {"role", "content"} dicts appended before the new prompt.
    """
    if ensure:
        ensure_gateway()
    resolved = resolve_model(model)
    url, body_model = _endpoint(resolved)
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})
    payload = {"model": body_model, "messages": messages}
    result = _post(url, payload, timeout)
    return extract_text(result)


def extract_text(result: dict) -> str:
    """Pull the assistant text out of an OpenAI-shaped response."""
    try:
        choices = result.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                joined = "".join(parts).strip()
                if joined:
                    return joined
    except Exception:
        pass
    if "error" in result:
        return f"[gateway error] {result['error']}"
    return json.dumps(result)[:2000]


def list_models() -> list[str]:
    base = base_url()
    try:
        with urllib.request.urlopen(f"{base}/v1/models", timeout=5) as resp:
            data = json.loads(resp.read() or b"{}")
        return [m.get("id", "") for m in data.get("data", []) if isinstance(m, dict)]
    except Exception:
        return []


def health() -> dict:
    base = base_url()
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=5) as resp:
            return json.loads(resp.read() or b"{}")
    except Exception as exc:
        return {"status": "down", "error": str(exc)}


if __name__ == "__main__":
    import sys
    print(ask(" ".join(sys.argv[1:]) or "Say OK."))
