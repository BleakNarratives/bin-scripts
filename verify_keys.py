#!/usr/bin/env python3
"""verify_keys.py -- probe every key in the vault and report what is actually live.

Read-only unless --apply. Never prints a key, only a masked fingerprint.

Status mapping, deliberately conservative:

  live            -> status "live"
  HTTP 401 / 403  -> status "dead"     (the key is not accepted)
  HTTP 402        -> status "unverified" (out of credits: the key is fine)
  anything else   -> status unchanged, reported only

A provider with no probe stays "unverified" forever rather than being called
live on the strength of being present.

Usage:  python3 bin/verify_keys.py [--apply] [--only groq,gemini]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_keys import NO_PROBE, PROBES, load_vault, mask, probe  # noqa: E402


def suggest(result: str) -> str | None:
    """Status the vault should carry, or None to leave it alone."""
    if result == "live":
        return "live"
    if result in ("HTTP_401", "HTTP_403"):
        return "dead"
    if result == "HTTP_402":
        return "unverified"
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify vault keys.")
    ap.add_argument("--vault", default=str(Path.home() / ".concierge" / "vault.json"))
    ap.add_argument("--apply", action="store_true", help="write status changes")
    ap.add_argument("--only", default="", help="comma-separated providers")
    a = ap.parse_args(argv)

    path = Path(a.vault).expanduser()
    blob = load_vault(path)
    keys = blob.get("keys") or {}
    wanted = {s.strip() for s in a.only.split(",") if s.strip()}

    rows, changes = [], 0
    legacy = [p for p, e in keys.items() if not isinstance(e, dict)]
    # A key filed under two providers is the signature of the old unanchored
    # ingest: one token, several slots, at most one of them usable. Detect it
    # by fingerprint so it is reported as the data defect it is.
    seen: dict[str, list[str]] = {}
    for provider, entry in keys.items():
        if not isinstance(entry, dict):
            continue
        first = str(entry.get("key") or "").split(",")[0].strip()
        if first:
            seen.setdefault(hashlib.sha256(first.encode()).hexdigest()[:8],
                            []).append(provider)
    duplicates = {fp: ps for fp, ps in seen.items() if len(ps) > 1}
    for provider in sorted(keys):
        entry = keys[provider]
        if not isinstance(entry, dict):
            # The old ingest script wrote `keys.COHERE_API_KEY = "..."`. Those
            # are invisible to every reader of the schema, so they are reported
            # here rather than silently skipped.
            print(f"{provider:14} LEGACY flat entry (string, not schema; not probed)")
            continue
        if wanted and provider not in wanted:
            continue
        pool = [k.strip() for k in str(entry.get("key") or "").split(",") if k.strip()]
        if not pool:
            print(f"{provider:14} no key")
            continue
        if provider not in PROBES:
            why = NO_PROBE.get(provider, "no validated probe endpoint")
            flag = " UNSUPPORTED" if entry.get("status") == "live" else ""
            print(f"{provider:14} {len(pool)} key(s)  no probe ({why}); "
                  f"left as {entry.get('status', 'unknown')}{flag}")
            continue
        result = probe(provider, pool[0])
        target = suggest(result)
        note = "" if target in (None, entry.get("status")) else f"  -> {target}"
        print(f"{provider:14} {len(pool)} key(s)  probe={result:<12}"
              f"status={entry.get('status', '?'):<11}{note}  {mask(pool[0])}")
        rows.append({"provider": provider, "probe": result, "suggest": target})
        if a.apply and target and entry.get("status") != target:
            entry["status"] = target
            entry["verified"] = date.today().isoformat()
            changes += 1

    if duplicates:
        print("\nDUPLICATE KEYS -- one key filed under several providers:")
        for fp, providers in sorted(duplicates.items()):
            print(f"  [{fp}] {', '.join(sorted(providers))}")
        print("  at most one of each group can be the real owner; the rest are "
              "misfiled by the old unanchored ingest")

    live = sum(1 for r in rows if r["probe"] == "live")
    skipped = len([p for p, e in keys.items()
                   if isinstance(e, dict) and p not in PROBES])
    print(f"\nprobed {len(rows)} provider(s): {live} live; "
          f"{skipped} without a validated probe; {len(legacy)} legacy entry(ies)")
    print("UNSUPPORTED means the entry says 'live' but nothing can check it; "
          "that is a claim, not a measurement.")
    if legacy:
        print("legacy entries need `--migrate-legacy` or manual placement:",
              ", ".join(sorted(legacy)))
    if a.apply:
        if changes:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(blob, indent=2), encoding="utf-8")
            tmp.replace(path)
            print(f"updated {changes} status field(s) in {path}")
        else:
            print("no status changes")
    else:
        print("read-only; re-run with --apply to record these statuses")
    return 0


if __name__ == "__main__":
    import telemetry
    telemetry.install("verify_keys")
    sys.exit(main())
