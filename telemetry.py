#!/usr/bin/env python3
"""telemetry.py -- one event per tool run, written after the tool exits.

Why this exists: ``usage_report.py`` could only say WIRED_UNMEASURED for every
component, because nothing on this machine recorded that a tool had ever run.
Static file dates and grep counts prove wiring; they cannot prove use.

Deliberate design choices:

  1. SEPARATE LOG from Thoth's own import log. ``recursive_engine`` writes
     thoth_bus.tsv with columns (stamp, available, detail) meaning "the bus
     imported / did not". Folding tool runs into that file would inflate the
     Thoth numbers with events that say nothing about the bus. This module
     writes bin_tools/events.tsv instead and records bus availability as one
     column of its own, so both questions stay answerable separately.

  2. NEVER RAISES. A telemetry failure must not change a tool's exit code or
     output. Every write is wrapped; the atexit handler swallows everything.

  3. HONEST COVERAGE. Only tools that call ``install()`` (or are run through
     ``bin/toolrun``) produce events. A tool that does not is absent from the
     log, which reads as "not instrumented", never as "never used". There is no
     machine-wide .pth hook here on purpose: that would alter every Python
     process on the box to solve a problem confined to ~10 scripts.

Usage in a tool:

    if __name__ == "__main__":
        import telemetry
        telemetry.install("ingest_keys")
        sys.exit(main())

CLI:

    python3 bin/telemetry.py --probe          # is the bus importable right now
    python3 bin/telemetry.py --log tool=x --rc 0 [--detail '...']
    python3 bin/telemetry.py --tail 5         # recent events
"""
from __future__ import annotations

import atexit
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOG = (Path.home() / "RootBase" / "logs" / "shared" / "bin_tools"
       / "events.tsv")
COLUMNS = ("stamp", "tool", "rc", "dur_s", "bus_available", "detail")


def _clean(value: object, limit: int = 200) -> str:
    """One TSV cell, whatever the caller passed. Tabs and newlines would split
    the row into the wrong columns, so they become spaces."""
    text = str(value)
    for ch in ("\t", "\r", "\n"):
        text = text.replace(ch, " ")
    return text[:limit]


def bus_available() -> tuple[bool, str]:
    """Can the Thoth bus be imported in this process, and why not if not."""
    try:
        import thoth_orchestrator.agent_bus  # type: ignore  # noqa: F401
        return True, ""
    except Exception as exc:  # noqa: BLE001 - any failure means "not available"
        return False, f"{type(exc).__name__}: {exc}"


def write_row(tool: str, rc: object, dur_s: object, detail: str = "",
              available: bool | None = None) -> bool:
    """Append one event. Returns False instead of raising on any failure."""
    try:
        if available is None:
            available, _ = bus_available()
        row = (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            _clean(tool, 60),
            _clean(rc, 12),
            _clean(dur_s, 12),
            str(int(bool(available))),
            _clean(detail),
        )
        path = Path(LOG)  # also accepts a plain string override
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\t".join(row) + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False


_installed: set[str] = set()


def install(tool: str, argv: list[str] | None = None) -> bool:
    """Register a post-execution event for this process. Idempotent.

    Exit code capture: ``sys.exit`` is wrapped so an explicit status is
    recorded; an uncaught exception is caught by ``sys.excepthook``. Reaching
    atexit without either means the interpreter is exiting 0, so that is the
    default. Known gap: ``raise SystemExit(2)`` (rather than ``sys.exit(2)``)
    bypasses both hooks and is recorded as 0. Use ``bin/toolrun`` when the exit
    status must be exact and you do not control the script.
    """
    if tool in _installed:
        return True
    _installed.add(tool)

    started = time.monotonic()
    state: dict[str, object] = {"rc": 0, "detail": ""}
    original_exit = sys.exit
    original_hook = sys.excepthook

    def _exit(code=0):  # mirrors sys.exit's signature loosely on purpose
        state["rc"] = code if isinstance(code, int) else 0
        return original_exit(code)

    def _hook(exc_type, exc, tb):
        state["rc"] = 1
        state["detail"] = f"exception {exc_type.__name__}: {exc}"
        return original_hook(exc_type, exc, tb)

    sys.exit = _exit          # type: ignore[assignment]
    sys.excepthook = _hook    # type: ignore[assignment]

    def _flush() -> None:
        try:
            detail = str(state["detail"] or "")
            if not detail and argv:
                detail = "argv " + " ".join(argv[:6])
            write_row(tool, state["rc"], round(time.monotonic() - started, 3),
                      detail)
        except Exception:  # noqa: BLE001
            pass

    atexit.register(_flush)
    return True


def tail(count: int = 5) -> list[str]:
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-count:]


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Tool-run telemetry.")
    ap.add_argument("--probe", action="store_true", help="report bus availability")
    ap.add_argument("--log", action="store_true", help="append one event")
    ap.add_argument("--tool", default="telemetry-cli")
    ap.add_argument("--rc", default="0")
    ap.add_argument("--detail", default="")
    ap.add_argument("--tail", type=int, default=0)
    ap.add_argument("--selfcheck", action="store_true",
                    help="write a canary event and read it back")
    a = ap.parse_args(argv)

    if a.probe:
        ok, why = bus_available()
        print(f"thoth_bus_importable={ok}" + (f" ({why})" if why else ""))
        return 0 if ok else 1

    if a.selfcheck:
        ok, why = bus_available()
        wrote = write_row("telemetry-selfcheck", 0, 0, "selfcheck", ok)
        back = [l for l in tail(20) if "\ttelemetry-selfcheck\t" in l]
        print(f"log={LOG}")
        print(f"wrote={wrote} readback={bool(back)}")
        if back:
            print("row:", back[-1])
        return 0 if (wrote and back) else 1

    if a.log:
        ok, why = bus_available()
        wrote = write_row(a.tool, a.rc, "0", a.detail, ok)
        if not wrote:
            print(f"could not write {LOG}", file=sys.stderr)
        return 0 if wrote else 1

    if a.tail:
        for line in tail(a.tail):
            print(line.replace("\t", " | "))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
