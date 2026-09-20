#!/usr/bin/env python3
"""Non-destructive key health checker.

Reads API keys from .concierge/vault.json, probes Cohere, DeepSeek, SambaNova,
and AIMLAPI endpoints, and writes a status matrix to logs/key_health.json.
No vault files are modified.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import urllib.error
import urllib.request


REPO_ROOT = Path(__file__).resolve().parent.parent
VAULT_PATH = REPO_ROOT / ".concierge" / "vault.json"
LOG_PATH = REPO_ROOT / "logs" / "key_health.json"

TIMEOUT_SECONDS = 10


PROVIDER_ENDPOINTS: dict[str, dict[str, Any]] = {
    "cohere": {
        "url": "https://api.cohere.ai/v1/models",
        "header": "Bearer",
        "env_keys": ["COHERE_API_KEY"],
        "vault_top_level": ["COHERE_API_KEY"],
        "label": "Cohere",
    },
    "deepseek": {
        "url": "https://api.deepseek.com/v1/models",
        "header": "Bearer",
        "env_keys": ["DEEPSEEK_API_KEY"],
        "vault_top_level": ["DEEPSEEK_API_KEY"],
        "label": "DeepSeek",
    },
    "sambanova": {
        "url": "https://api.sambanova.ai/v1/models",
        "header": "Bearer",
        "env_keys": ["SAMBANOVA_API_KEY"],
        "vault_top_level": ["SAMBANOVA_API_KEY"],
        "label": "SambaNova",
    },
    "aimlapi": {
        "url": "https://api.aimlapi.com/v1/models",
        "header": "Bearer",
        "env_keys": ["AIMLAPI_KEY"],
        "vault_top_level": ["AIMLAPI_KEY"],
        "label": "AIMLAPI",
    },
}


def load_vault(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Vault not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def resolve_key(vault: dict[str, Any], candidates: list[str]) -> str | None:
    env_value = os.environ.get(candidates[0]) if candidates else None
    if env_value:
        return env_value.strip()
    for key_name in candidates:
        value = vault.get(key_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        nested = vault.get("keys", {}).get(key_name)
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
        if isinstance(nested, dict):
            inner = nested.get("key")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return None


def probe_endpoint(url: str, key: str, header_format: str) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if header_format == "Bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif header_format == "x-api-key":
        headers["x-api-key"] = key
    else:
        headers["Authorization"] = f"{header_format} {key}"

    request = urllib.request.Request(url, headers=headers, method="GET")
    start = datetime.now(timezone.utc)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
            elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
            return {
                "ok": True,
                "status_code": response.status,
                "latency_ms": elapsed_ms,
                "error": None,
                "response_preview": body[:200],
            }
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return {
            "ok": False,
            "status_code": exc.code,
            "latency_ms": elapsed_ms,
            "error": str(exc.reason),
            "response_preview": body,
        }
    except Exception as exc:
        elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": elapsed_ms,
            "error": str(exc),
            "response_preview": "",
        }


def safe_prefix(key: str | None) -> str:
    if not key:
        return "MISSING"
    if len(key) <= 10:
        return f"{key[:4]}***"
    return f"{key[:10]}***"


def build_matrix(vault: dict[str, Any]) -> dict[str, Any]:
    matrix: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vault_path": str(VAULT_PATH),
        "providers": {},
    }

    for provider_name, config in PROVIDER_ENDPOINTS.items():
        raw_key = resolve_key(vault, config["vault_top_level"])
        provider_entry: dict[str, Any] = {
            "label": config["label"],
            "endpoint": config["url"],
            "key_prefix": safe_prefix(raw_key),
            "has_key": bool(raw_key),
        }

        if raw_key:
            result = probe_endpoint(config["url"], raw_key, config["header"])
            provider_entry.update(result)
            provider_entry["status"] = "ok" if result["ok"] else "error"
        else:
            provider_entry.update(
                {
                    "ok": False,
                    "status_code": None,
                    "latency_ms": None,
                    "error": "missing key",
                    "response_preview": "",
                    "status": "skipped",
                }
            )

        matrix["providers"][provider_name] = provider_entry

    return matrix


def write_log(matrix: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(matrix, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    tmp_path.replace(path)


def main() -> int:
    try:
        vault = load_vault(VAULT_PATH)
    except Exception as exc:
        print(f"Failed to load vault: {exc}", file=sys.stderr)
        return 1

    matrix = build_matrix(vault)

    try:
        write_log(matrix, LOG_PATH)
    except Exception as exc:
        print(f"Failed to write log: {exc}", file=sys.stderr)
        return 1

    for provider, entry in matrix["providers"].items():
        status = entry.get("status", "unknown")
        latency = entry.get("latency_ms")
        latency_str = f"{latency}ms" if latency is not None else "n/a"
        print(f"{provider:10s} | {status:8s} | {latency_str:>7s} | {entry.get('error', '')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
