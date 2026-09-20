#!/usr/bin/env python3

# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: armory
# DEPS: stdlib only
# ROLE: Provenance trace log for every gateway call — capture, redact, account
# AUTHOR: Bleak
# SESSION: 2026-09-15
# TIER: 3
# [/DNA_TAG]

"""ai_trace — what actually happened on every model call.

Three jobs, in this order and no other:

  1. record()     one JSONL line per gateway call: who answered, tokens, latency, cost
  2. redact()     strips personal data and credentials BEFORE anything is written
  3. summarize()  per-provider spend and latency for the `ai --spend` report

ORDER MATTERS. Redaction runs inside record(), on the way to disk. There is no
code path that writes raw prompt text to the trace store. A redaction pass that
runs later is not a safety measure — a trace already on disk cannot be
un-leaked, and this store is intended to be training-corpus input.

TRUST BOUNDARY. Prompt text is untrusted: it routinely carries paths, emails,
tokens and customer data. The store is therefore treated as a place personal
data must not reach, not as a place to clean up afterwards.

The trace is metadata-first. With capture = "off" (or enabled = false) nothing
is written at all. With capture = "redacted" (the default) prompt and response
are stored with redactions applied and the redaction counts kept alongside, so
you can see what was removed without ever seeing it. capture = "raw" stores
text unredacted and is opt-in only.

A trace failure must never fail a model call: every entry point swallows its
own exceptions.

Stdlib only. Run: python3 ~/bin/ai_trace.py summary --days 7
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

SCHEMA = "ai.trace/1"

CONFIG_PATH = Path.home() / ".config" / "ai" / "config.toml"
DEFAULT_TRACE_DIR = Path.home() / ".local" / "state" / "ai" / "traces"

CAPTURE_OFF = "off"
CAPTURE_REDACTED = "redacted"
CAPTURE_RAW = "raw"
CAPTURE_MODES = (CAPTURE_OFF, CAPTURE_REDACTED, CAPTURE_RAW)

# Bounded payloads: a 200k-char prompt is a corpus item, not a trace line, and
# an unbounded store makes the summary scan quadratic for no benefit.
MAX_FIELD_CHARS = 8000


# ── config ───────────────────────────────────────────────────────────────────

_cfg_cache: Dict[str, Any] = {"mtime": None, "data": {}}


def _config() -> Dict[str, Any]:
    """Read ~/.config/ai/config.toml, cached by mtime.

    Cached because the gateway calls this on every request and the gateway is
    long-lived; keyed by mtime so editing the config applies without a restart.
    """
    try:
        st = CONFIG_PATH.stat()
    except OSError:
        return {}
    if _cfg_cache["mtime"] == st.st_mtime:
        return _cfg_cache["data"]
    try:
        import tomllib
        with CONFIG_PATH.open("rb") as fh:
            data = tomllib.load(fh)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    _cfg_cache["mtime"] = st.st_mtime
    _cfg_cache["data"] = data
    return data


def trace_config() -> Dict[str, Any]:
    cfg = _config().get("trace")
    return cfg if isinstance(cfg, dict) else {}


def capture_mode() -> str:
    """Resolve the effective capture mode. Safe default: redacted."""
    cfg = trace_config()
    if cfg.get("enabled") is False:
        return CAPTURE_OFF
    mode = str(cfg.get("capture", CAPTURE_REDACTED)).strip().lower()
    return mode if mode in CAPTURE_MODES else CAPTURE_REDACTED


def trace_dir(override: Optional[Path] = None) -> Path:
    """Where traces land. env > config > default, in that order."""
    if override is not None:
        return Path(override)
    env = os.environ.get("AI_TRACE_DIR")
    if env:
        return Path(env)
    cfg = trace_config().get("dir")
    if cfg:
        return Path(os.path.expanduser(str(cfg)))
    return DEFAULT_TRACE_DIR


def _max_field_chars() -> int:
    try:
        return max(256, int(trace_config().get("max_field_chars", MAX_FIELD_CHARS)))
    except Exception:
        return MAX_FIELD_CHARS


# ── redaction ────────────────────────────────────────────────────────────────

# Ordered most-specific-first. Each entry is (label, pattern). The replacement
# is a stable label, never a fragment of the match, so a trace stays readable
# and countable without any part of the original being recoverable from it.
_REDACTIONS: List[Tuple[str, "re.Pattern[str]"]] = [
    # Credentials before anything else: a JWT is dots and base64 that the later
    # generic patterns would chew into fragments, leaving a usable remainder.
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}")),
    ("api_key", re.compile(
        r"\b(?:gsk_|csk-|sk-or-v1-|sk-ant-|sk-proj-|sk-live-|sk-test-|xai-|nvapi-|"
        r"r8_|pplx-|hf_|fw_|tgp_v1_|AIza|AKIA|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)"
        r"[A-Za-z0-9_\-]{10,}")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    # Long high-entropy blobs: secrets with no recognisable prefix. Deliberately
    # >=48 chars to limit over-redaction of ordinary code and prose; the cost of
    # a false positive is a mangled corpus line, so it is set where it is rare.
    ("secret", re.compile(r"\b[A-Za-z0-9+/]{48,}={0,2}\b")),
    ("secret", re.compile(r"\b[0-9a-fA-F]{48,}\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("card", re.compile(r"\b(?:\d[ \-]?){13,19}\b")),
    ("phone", re.compile(r"\b(?:\+?1[ .\-])?\(?\d{3}\)?[ .\-]\d{3}[ .\-]\d{4}\b")),
    # Identifies the machine's owner, which is in nearly every trace otherwise.
    ("home_path", re.compile(r"/(?:home|Users)/[A-Za-z0-9._\-]+")),
]


def redact_text(text: str, counts: Optional[Dict[str, int]] = None) -> str:
    """Redact one string. Returns the redacted text; counts accumulates labels."""
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    out = text
    for label, pattern in _REDACTIONS:
        def _sub(_m: "re.Match[str]", _label: str = label) -> str:
            if counts is not None:
                counts[_label] = counts.get(_label, 0) + 1
            return f"[{_label}]"
        out = pattern.sub(_sub, out)
    return out


def redact(obj: Any, counts: Optional[Dict[str, int]] = None) -> Any:
    """Recursively redact strings inside dicts/lists (gateway bodies are nested)."""
    if isinstance(obj, str):
        return redact_text(obj, counts)
    if isinstance(obj, dict):
        return {k: redact(v, counts) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, counts) for v in obj]
    if isinstance(obj, tuple):
        return [redact(v, counts) for v in obj]
    return obj


def key_fingerprint(key: str) -> str:
    """Non-reversible pool-key identifier.

    Deliberately NOT the gateway's `_tag()`, which reveals the first and last
    four characters. A trace store outlives the log file and is meant to be
    shippable as corpus, so it gets a hash prefix that cannot be walked back.
    """
    if not key:
        return ""
    return "k" + hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:10]


def _sha(text: Any) -> str:
    """Hash of the *stored* (already redacted) text — stable for dedupe."""
    if not isinstance(text, str):
        try:
            text = json.dumps(text, sort_keys=True)
        except Exception:
            text = str(text)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _truncate(text: Any, limit: Optional[int] = None) -> Any:
    limit = limit or _max_field_chars()
    if isinstance(text, str):
        return text if len(text) <= limit else text[:limit] + f"…[truncated {len(text) - limit}c]"
    if isinstance(text, (dict, list)):
        try:
            blob = json.dumps(text, ensure_ascii=False)
        except Exception:
            return str(text)[:limit]
        return blob if len(blob) <= limit else blob[:limit] + f"…[truncated {len(blob) - limit}c]"
    return text


def _merge_counts(*dicts: Dict[str, int]) -> Dict[str, int]:
    total: Dict[str, int] = {}
    for d in dicts:
        for k, v in (d or {}).items():
            total[k] = total.get(k, 0) + v
    return total


# ── cost ─────────────────────────────────────────────────────────────────────

# Honest by default. Only free tiers are priced at 0.0 here; anything else is
# None ("unknown") rather than a guessed number, because a wrong cost in a
# spend report is worse than a blank one. Add real rates under [prices] in
# ~/.config/ai/config.toml as { "<model-substring>" = [input_per_1m, output_per_1m] }.
FREE_PROVIDERS = {"groq", "cerebras", "gemini", "puter"}


def estimate_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int
                  ) -> Tuple[Optional[float], str]:
    """Returns (cost_usd, basis). basis is free-tier | priced | unknown."""
    if model.endswith(":free"):
        return 0.0, "free-tier"
    if provider in FREE_PROVIDERS:
        return 0.0, "free-tier"
    prices = _config().get("prices")
    if isinstance(prices, dict):
        for needle, rate in prices.items():
            if str(needle).lower() in (model or "").lower() and isinstance(rate, (list, tuple)) and len(rate) == 2:
                try:
                    inp, out = float(rate[0]), float(rate[1])
                except (TypeError, ValueError):
                    continue
                return (prompt_tokens / 1_000_000 * inp) + (completion_tokens / 1_000_000 * out), "priced"
    return None, "unknown"


# ── record ───────────────────────────────────────────────────────────────────

def record(*, provider: str, model: str, status: int, latency_ms: int,
           tokens: Optional[Dict[str, int]] = None,
           prompt: Any = None, response: Any = None,
           attempts: Optional[int] = None, key_tag: Optional[str] = None,
           requested_provider: Optional[str] = None,
           requested_model: Optional[str] = None,
           error: Optional[str] = None,
           capture: Optional[str] = None,
           trace_dir_override: Optional[Path] = None,
           ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Append one trace record. Returns the record, or None if not written.

    Never raises: a broken trace store must not break a working gateway.
    """
    try:
        mode = capture or capture_mode()
        if mode == CAPTURE_OFF:
            return None

        tokens = tokens or {}
        prompt_tokens = int(tokens.get("prompt", tokens.get("prompt_tokens", 0)) or 0)
        completion_tokens = int(tokens.get("completion", tokens.get("completion_tokens", 0)) or 0)
        total_tokens = int(tokens.get("total", tokens.get("total_tokens",
                            prompt_tokens + completion_tokens)) or 0)

        cost, basis = estimate_cost(provider, model, prompt_tokens, completion_tokens)

        when = float(ts if ts is not None else time.time())
        rec: Dict[str, Any] = {
            "schema": SCHEMA,
            "ts": round(when, 3),
            "iso": datetime.fromtimestamp(when, timezone.utc).isoformat(timespec="seconds"),
            "provider": provider,
            "model": model,
            "status": int(status),
            "latency_ms": int(latency_ms),
            "attempts": attempts,
            "key_tag": key_tag,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost,
            "cost_basis": basis,
            "error": (str(error)[:500] if error else None),
        }
        if requested_provider and requested_provider != provider:
            rec["requested_provider"] = requested_provider
        if requested_model and requested_model != model:
            rec["requested_model"] = requested_model

        counts: Dict[str, int] = {}
        if prompt is not None or response is not None:
            if mode == CAPTURE_RAW:
                stored_prompt, stored_response = _truncate(prompt), _truncate(response)
                rec["capture"] = CAPTURE_RAW
            else:
                stored_prompt = _truncate(redact(prompt, counts))
                stored_response = _truncate(redact(response, counts))
                rec["capture"] = CAPTURE_REDACTED
            rec["prompt"] = stored_prompt
            rec["response"] = stored_response
            rec["prompt_sha256"] = _sha(stored_prompt)
            rec["response_sha256"] = _sha(stored_response)
            if counts:
                rec["redactions"] = _merge_counts(counts)

        path = _day_path(when, trace_dir_override)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        return rec
    except Exception:
        # A trace failure is never a call failure.
        return None


def _day_path(when: float, override: Optional[Path] = None) -> Path:
    day = datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%d")
    return trace_dir(override) / f"{day}.jsonl"


# ── read / summarize ─────────────────────────────────────────────────────────

def iter_records(days: Optional[int] = None,
                 trace_dir_override: Optional[Path] = None) -> Iterator[Dict[str, Any]]:
    """Yield trace records, oldest first. Corrupt lines are skipped, not fatal."""
    root = trace_dir(trace_dir_override)
    if not root.is_dir():
        return
    cutoff = None if not days else time.time() - days * 86400
    for path in sorted(root.glob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    if cutoff is not None:
                        try:
                            if float(rec.get("ts", 0)) < cutoff:
                                continue
                        except (TypeError, ValueError):
                            continue
                    yield rec
        except OSError:
            continue


def _percentile(values: List[float], pct: float) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    idx = int(round((pct / 100.0) * (len(ordered) - 1)))
    idx = min(len(ordered) - 1, max(0, idx))
    return int(round(ordered[idx]))


def summarize(days: int = 7, trace_dir_override: Optional[Path] = None) -> Dict[str, Any]:
    """Per-provider calls, tokens, cost and latency over the window."""
    rows: Dict[str, Dict[str, Any]] = {}
    calls = 0
    total_tokens = 0
    cost_sum = 0.0
    cost_known = True
    errors = 0

    for rec in iter_records(days=days, trace_dir_override=trace_dir_override):
        calls += 1
        provider = str(rec.get("provider") or "?")
        model = str(rec.get("model") or "?")
        row = rows.setdefault(provider, {
            "provider": provider, "calls": 0, "ok": 0, "errors": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "cost_usd": 0.0, "cost_known": True,
            "latencies": [], "models": {}, "redactions": {},
        })
        row["calls"] += 1
        try:
            status = int(rec.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        if status == 200:
            row["ok"] += 1
        else:
            row["errors"] += 1
            errors += 1
        pt = int(rec.get("prompt_tokens") or 0)
        ct = int(rec.get("completion_tokens") or 0)
        tt = int(rec.get("total_tokens") or (pt + ct))
        row["prompt_tokens"] += pt
        row["completion_tokens"] += ct
        row["total_tokens"] += tt
        total_tokens += tt
        try:
            row["latencies"].append(float(rec.get("latency_ms") or 0))
        except (TypeError, ValueError):
            pass
        cost = rec.get("cost_usd")
        if isinstance(cost, (int, float)):
            row["cost_usd"] += float(cost)
            cost_sum += float(cost)
        else:
            row["cost_known"] = False
            cost_known = False
        row["models"][model] = row["models"].get(model, 0) + 1
        for label, n in (rec.get("redactions") or {}).items():
            row["redactions"][label] = row["redactions"].get(label, 0) + int(n or 0)

    providers: List[Dict[str, Any]] = []
    for row in rows.values():
        lat = row.pop("latencies")
        row["p50_ms"] = _percentile(lat, 50)
        row["p95_ms"] = _percentile(lat, 95)
        row["top_model"] = max(row["models"].items(), key=lambda kv: kv[1])[0] if row["models"] else "?"
        providers.append(row)
    providers.sort(key=lambda r: (-r["calls"], r["provider"]))

    return {
        "days": days,
        "calls": calls,
        "errors": errors,
        "total_tokens": total_tokens,
        "cost_usd": round(cost_sum, 4) if calls else 0.0,
        "cost_complete": cost_known,
        "providers": providers,
    }


def format_spend(report: Dict[str, Any]) -> str:
    """Render summarize() as a fixed-width table."""
    providers = report.get("providers") or []
    if not providers:
        return (f"no traces in the last {report.get('days')}d "
                f"({trace_dir()}) — capture is {capture_mode()!r}")
    head = (f"{'provider':<12} {'calls':>6} {'ok':>5} {'err':>4} "
            f"{'tok_in':>9} {'tok_out':>9} {'p50':>7} {'p95':>7} {'cost':>10}")
    lines = [head, "-" * len(head)]
    for r in providers:
        cost = "unknown" if not r.get("cost_known") else f"${r['cost_usd']:.4f}"
        lines.append(
            f"{r['provider']:<12} {r['calls']:>6} {r['ok']:>5} {r['errors']:>4} "
            f"{r['prompt_tokens']:>9} {r['completion_tokens']:>9} "
            f"{(r.get('p50_ms') or 0):>6}ms {(r.get('p95_ms') or 0):>6}ms {cost:>10}")
    total = f"${report['cost_usd']:.4f}" if report.get("cost_complete") else "unknown"
    lines.append("-" * len(head))
    lines.append(f"{'TOTAL':<12} {report['calls']:>6} {'':>5} {report['errors']:>4} "
                 f"{'':>9} {report['total_tokens']:>9}  tokens   {'':>7} {total:>10}")
    if not report.get("cost_complete"):
        lines.append("cost 'unknown' where no rate is configured — add [prices] to "
                     "~/.config/ai/config.toml rather than trusting a guess")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="ai_trace",
                                 description="Provenance trace store for gateway calls.")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("summary", help="per-provider spend and latency")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--json", action="store_true")
    sub.add_parser("status", help="capture mode and store location")
    r = sub.add_parser("redact", help="show what redaction does to stdin (nothing is stored)")
    r.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "status" or args.cmd is None:
        print(json.dumps({
            "capture": capture_mode(),
            "trace_dir": str(trace_dir()),
            "config": str(CONFIG_PATH),
            "records": sum(1 for _ in iter_records()),
        }, indent=2))
        return 0

    if args.cmd == "summary":
        report = summarize(days=args.days)
        print(json.dumps(report, indent=2) if args.json else format_spend(report))
        return 0

    if args.cmd == "redact":
        counts: Dict[str, int] = {}
        cleaned = redact(sys.stdin.read(), counts)
        print(json.dumps({"redacted": cleaned, "counts": counts}, indent=2) if args.json else cleaned)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
