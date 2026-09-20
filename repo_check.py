#!/usr/bin/env python3

# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: armory
# DEPS: stdlib only
# ROLE: Pre-landing repo check - emoji in first-party code, Python compiles, shell parses
# AUTHOR: Bleak
# SESSION: 2026-09-15
# TIER: 3
# [/DNA_TAG]

"""
repo_check - fail a change before it lands.

Checks three things and exits non-zero if any of them fails:

  1. emoji in first-party source/config files
  2. every .py parses and compiles
  3. every .sh parses under `bash -n`

Read-only and deterministic: nothing is written, nothing is fetched, no
provider is contacted. Compilation uses the built-in `compile()` on the source
bytes, so no __pycache__ is created either.

Usage:
  python3 bin/repo_check.py                  # default roots
  python3 bin/repo_check.py --paths src bin  # narrower
  python3 bin/repo_check.py --quiet          # failures only
  python3 bin/repo_check.py --no-shell       # skip the bash -n pass

Emoji were previously sprinkled through this tree by coding agents. They are
not a style opinion here: the user asked for them out of the code, so a check
that lets them back in is the point of this script.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOTS = (
    "src", "bin", "tools", "tests", "Whorl", "MikeySwarm",
    "passive_income_swarm", "RootBase", "The-Werkz", "prototypes",
    "projects", "MemGuard",
)

# Vendored and generated trees. Never sweep into these.
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages",
    "dist", "build", ".tox", ".eggs",
}

CODE_EXT = {
    ".py", ".sh", ".bash", ".zsh", ".toml", ".yml", ".yaml",
    ".cfg", ".ini", ".service", ".timer", ".conf",
}

# True emoji. Deliberately excludes box-drawing (U+2500-257F), arrows and
# dashes - those are used as section separators throughout this tree and are
# not what the user objected to.
EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # pictographs, emoticons, symbols
    "\u2600-\u27BF"           # misc symbols + dingbats (check marks, warning)
    "\u2B00-\u2BFF"           # arrows and stars
    "\uFE0F"                  # variation selector (emoji presentation)
    "\u2049\u203C"            # double exclamation / question
    "\u200D"                  # zero-width joiner
    "]"
)


def walk(roots: list[str]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        base = Path(root)
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if SKIP_DIRS & set(path.parts):
                continue
            found.append(path)
    return sorted(set(found))


def check_emoji(files: list[Path]) -> list[tuple[Path, int, str]]:
    hits: list[tuple[Path, int, str]] = []
    for path in files:
        if path.suffix not in CODE_EXT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            found = EMOJI_RE.findall(line)
            if found:
                hits.append((path, lineno, "".join(sorted(set(found)))))
    return hits


def check_python(files: list[Path]) -> list[tuple[Path, str]]:
    failures: list[tuple[Path, str]] = []
    for path in files:
        if path.suffix != ".py":
            continue
        try:
            source = path.read_bytes()
        except OSError as exc:
            failures.append((path, f"unreadable: {exc}"))
            continue
        try:
            # compile() honours any coding cookie on the bytes and writes nothing.
            compile(source, str(path), "exec")
        except SyntaxError as exc:
            failures.append((path, f"line {exc.lineno}: {exc.msg}"))
        except ValueError as exc:
            failures.append((path, f"invalid source: {exc}"))
    return failures


def check_shell(files: list[Path]) -> list[tuple[Path, str]]:
    failures: list[tuple[Path, str]] = []
    for path in files:
        if path.suffix not in {".sh", ".bash", ".zsh"}:
            continue
        try:
            proc = subprocess.run(
                ["bash", "-n", str(path)],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append((path, f"could not run bash -n: {exc}"))
            continue
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            failures.append((path, detail[0] if detail else "bash -n failed"))
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description="Pre-landing repo check")
    ap.add_argument("--paths", nargs="+", default=list(DEFAULT_ROOTS),
                    help="roots to scan (default: the first-party tree)")
    ap.add_argument("--quiet", action="store_true", help="print failures only")
    ap.add_argument("--no-shell", action="store_true", help="skip the bash -n pass")
    args = ap.parse_args()

    files = walk(args.paths)
    if not files:
        print("repo_check: nothing to scan - did you pass the right --paths?")
        return 1

    emoji = check_emoji(files)
    py_fail = check_python(files)
    sh_fail = [] if args.no_shell else check_shell(files)

    if emoji:
        print(f"\nEMOJI in first-party code ({len(emoji)} line(s)):")
        for path, lineno, chars in emoji[:60]:
            print(f"  {path}:{lineno}  {chars}")
        if len(emoji) > 60:
            print(f"  ... and {len(emoji) - 60} more")

    if py_fail:
        print(f"\nPYTHON does not compile ({len(py_fail)} file(s)):")
        for path, detail in py_fail[:60]:
            print(f"  {path}  {detail}")
        if len(py_fail) > 60:
            print(f"  ... and {len(py_fail) - 60} more")

    if sh_fail:
        print(f"\nSHELL does not parse ({len(sh_fail)} file(s)):")
        for path, detail in sh_fail[:60]:
            print(f"  {path}  {detail}")
        if len(sh_fail) > 60:
            print(f"  ... and {len(sh_fail) - 60} more")

    bad = len(emoji) + len(py_fail) + len(sh_fail)
    if not args.quiet:
        n_py = sum(1 for f in files if f.suffix == ".py")
        n_sh = sum(1 for f in files if f.suffix in {".sh", ".bash", ".zsh"})
        print(
            f"\nrepo_check: {n_py} .py, {n_sh} shell script(s), "
            f"{len(files)} file(s) scanned -> "
            + (f"{bad} problem(s)" if bad else "clean")
        )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
