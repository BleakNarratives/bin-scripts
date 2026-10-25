#!/usr/bin/env python3
"""usage_report.py -- how much is a component actually used? With evidence.

Three kinds of evidence, kept separate because they mean different things:

  static      files, size, last modified
  reference   how many active files (bin, src, projects, tools) mention it
  runtime     lines in an event log, when the component writes one

A component is only "active" when runtime evidence exists. References alone
prove wiring, not use: this is the same distinction as a passing test versus a
test that can fail.

Usage:  python3 bin/usage_report.py [--json] [--since-days 30]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
ACTIVE_DIRS = ("bin", "src", "projects", "tools")
BUS_LOG = HOME / "RootBase" / "logs" / "shared" / "recursive_engine" / "thoth_bus.tsv"
TOOL_LOG = HOME / "RootBase" / "logs" / "shared" / "bin_tools" / "events.tsv"

COMPONENTS = [
    {
        "name": "thoth",
        "paths": ["thoth_orchestrator"],
        "match": "thoth",
        "note": "orchestration bus + motherbrain, imported by sovereign",
    },
    {
        "name": "hermes",
        "paths": ["thoth_orchestrator/hermes.sh",
                  "thoth_orchestrator/hermes_module",
                  ".hermes/config.yaml"],
        "match": "hermes",
        "note": "lives inside thoth; config checked by cli_doctor",
    },
    {
        "name": "zeroclaw",
        "paths": ["zeroclaw-android"],
        "match": "zeroclaw",
        "note": "android claw variant",
    },
    {
        "name": "openclaw",
        "paths": ["openclaw_ae_v3.html"],
        "match": "openclaw",
        "note": "single page, not a runtime",
    },
]


def mtime_days(paths: list[Path]) -> float | None:
    stamps = []
    for base in paths:
        if base.is_file():
            stamps.append(base.stat().st_mtime)
        elif base.is_dir():
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
                for f in files:
                    try:
                        stamps.append((Path(root) / f).stat().st_mtime)
                    except OSError:
                        pass
    if not stamps:
        return None
    return round((time.time() - max(stamps)) / 86400, 1)


def size_and_files(paths: list[Path]) -> tuple[float, int]:
    total, py = 0, 0
    for base in paths:
        if base.is_file():
            total += base.stat().st_size
            py += int(base.suffix == ".py")
        elif base.is_dir():
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
                for f in files:
                    p = Path(root) / f
                    try:
                        total += p.stat().st_size
                    except OSError:
                        continue
                    py += int(p.suffix == ".py")
    return round(total / 1_048_576, 1), py


def reference_count(term: str) -> int:
    cmd = ["grep", "-ril", term, *ACTIVE_DIRS]
    try:
        out = subprocess.run(cmd, cwd=HOME, capture_output=True, text=True,
                             timeout=30)
        return len([l for l in out.stdout.splitlines() if l.strip()])
    except (subprocess.SubprocessError, OSError):
        return 0


def bus_events(since_days: int) -> dict:
    """Thoth's own import log: available vs not, within the window."""
    if not BUS_LOG.is_file():
        return {"log": str(BUS_LOG), "available": 0, "unavailable": 0, "last": None}
    cutoff = time.time() - since_days * 86400
    ok = bad = 0
    last = None
    for line in BUS_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            stamp = time.mktime(time.strptime(parts[0][:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            continue
        if stamp < cutoff:
            continue
        last = parts[0]
        if parts[1].strip() == "1":
            ok += 1
        else:
            bad += 1
    return {"log": str(BUS_LOG), "available": ok, "unavailable": bad, "last": last}


def tool_runs(since_days: int) -> list[dict]:
    """Per-tool run counts from the telemetry hook.

    Separate from BUS_LOG on purpose: a tool run says nothing about whether the
    Thoth bus imported, so folding the two together would inflate the Thoth
    number with events that do not measure it.
    """
    if not TOOL_LOG.is_file():
        return []
    cutoff = time.time() - since_days * 86400
    counts: dict[str, dict] = {}
    for line in TOOL_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        try:
            stamp = time.mktime(time.strptime(parts[0][:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            continue
        if stamp < cutoff:
            continue
        row = counts.setdefault(parts[1], {"tool": parts[1], "runs": 0,
                                          "failures": 0, "last": parts[0]})
        row["runs"] += 1
        if parts[2].strip() not in ("0", ""):
            row["failures"] += 1
        row["last"] = parts[0]
    return sorted(counts.values(), key=lambda r: (-r["runs"], r["tool"]))


def verdict(static_days, refs, runtime) -> str:
    if runtime and (runtime.get("available", 0) + runtime.get("unavailable", 0)) > 0:
        return "ACTIVE"
    if refs > 0:
        return "WIRED_UNMEASURED"
    if static_days is not None and static_days < 45:
        return "RECENT_BUT_UNREFERENCED"
    return "DORMANT"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Measure component usage.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--since-days", type=int, default=30)
    a = ap.parse_args(argv)

    rows = []
    for comp in COMPONENTS:
        paths = [HOME / p for p in comp["paths"]]
        existing = [p for p in paths if p.exists()]
        days = mtime_days(existing) if existing else None
        mb, pys = size_and_files(existing) if existing else (0.0, 0)
        refs = reference_count(comp["match"])
        runtime = bus_events(a.since_days) if comp["name"] == "thoth" else {}
        rows.append({
            "name": comp["name"],
            "exists": bool(existing),
            "missing_paths": [str(p.relative_to(HOME)) for p in paths if not p.exists()],
            "last_change_days": days,
            "size_mb": mb,
            "py_files": pys,
            "references": refs,
            "runtime": runtime,
            "verdict": verdict(days, refs, runtime),
            "note": comp["note"],
        })

    if a.json:
        print(json.dumps(rows, indent=2))
        return 0

    print(f"{'component':<11}{'exists':<8}{'last change':<13}{'refs':<6}"
          f"{'py':<4}{'runtime':<10}verdict")
    for r in rows:
        last = f"{r['last_change_days']:.0f}d ago" if r["last_change_days"] is not None else "-"
        rt = "-"
        if r["runtime"]:
            rt = f"{r['runtime']['available']}/{r['runtime']['unavailable']}"
        print(f"{r['name']:<11}{str(r['exists']):<8}{last:<13}{r['references']:<6}"
              f"{r['py_files']:<4}{rt:<10}{r['verdict']}")
        if r["missing_paths"]:
            print(f"{'':11}missing: {', '.join(r['missing_paths'])}")

    print("\nruntime column is available/unavailable import events in the window")
    print("WIRED_UNMEASURED means something imports it but nothing records use;")
    print("treat that as unknown, not as working.")

    runs = tool_runs(a.since_days)
    print(f"\noperational tool runs, last {a.since_days}d "
          f"({TOOL_LOG.relative_to(HOME)}):")
    if not runs:
        print("  none recorded. That means no instrumented tool has run in this")
        print("  window -- not that nothing is used. Only tools that call")
        print("  telemetry.install() or run via bin/toolrun appear here.")
    else:
        print(f"  {'tool':<22}{'runs':<6}{'failed':<8}last")
        for r in runs:
            print(f"  {r['tool']:<22}{r['runs']:<6}{r['failures']:<8}{r['last']}")
    return 0


if __name__ == "__main__":
    import telemetry
    telemetry.install("usage_report", sys.argv[1:])
    sys.exit(main())
