#!/usr/bin/env python3

# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: workspace-hygiene
# DEPS: ast,os,sys
# ROLE: Static error-map scanner for the Crostini codebase
# AUTHOR: Bleak
# SESSION: 2026-09-11
# TIER: 4
# [/DNA_TAG]

"""
triage.py — static error-map scanner for a messy Crostini codebase.

Walks a tree, runs `ruff check --output-format=json` on every .py file
(excluding venvs / caches / archives), and produces a categorized report
so you know which files are broken and by what category.

Single-file, stdlib-only. Run:   triage [root] [--out REPORT.md]
"""
import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

RUFF = os.path.expanduser("~/.local/bin/ruff")

# Directory names to skip entirely (case-insensitive substring match on path parts).
EXCLUDE_DIR_PARTS = {
    "venv", "-env", "env-", "_env", "__pycache__", ".git", "node_modules",
    "site-packages", "dist-packages", ".tox", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", "UNPACKED", ".cache", ".venv", "build", "egg-info",
    ".ipynb_checkpoints", "vendor",
}
EXCLUDE_PATH_SUBSTR = ("/.local/lib/", "/.local/share/", "/.cargo/", "/.npm-global/")

# Skip dot-directories by default (.git, .cache, .config, .codebuddy, ._archive,
# ._incoming, .venv, .local, .cargo, etc.) — these are config/cache/archive, not
# source. Override with --include-dotdirs.
EXCLUDE_DOTDIRS = True
# Skip files larger than this — giant .py files are almost always concatenated
# "master library" dumps or data, not hand-written source. One 158k-issue file
# shouldn't dominate a triage report. Override with --include-large.
MAX_FILE_BYTES = 100_000

RUFF_RULE_NAMES = {
    "E999": "SyntaxError",
    "F401": "unused-import",
    "F403": "wildcard-import",
    "F405": "name-from-wildcard",
    "F404": "late-future-import",
    "F811": "redefinition",
    "F821": "undefined-name",
    "F822": "undefined-name-__all__",
    "F823": "undefined-local",
    "F841": "unused-local-var",
    "F901": "raise-not-implemented",
    "E711": "comparison-to-None",
    "E712": "comparison-to-True/False",
    "E722": "bare-except",
    "E902": "io-error",
}


def is_excluded(path: Path) -> bool:
    parts = [p.lower() for p in path.parts]
    for part in parts:
        for bad in EXCLUDE_DIR_PARTS:
            if bad.lower() in part:
                return True
        if EXCLUDE_DOTDIRS and part.startswith(".") and part != ".":
            return True
    s = str(path)
    for sub in EXCLUDE_PATH_SUBSTR:
        if sub in s:
            return True
    return False


def collect_py_files(root: Path):
    files = []
    skipped = 0
    skipped_large = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # prune in place
        dirnames[:] = [d for d in dirnames if not is_excluded(Path(dirpath) / d)]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = Path(dirpath) / fn
            if is_excluded(p):
                skipped += 1
                continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    skipped_large += 1
                    continue
            except OSError:
                skipped += 1
                continue
            files.append(str(p))
    return files, skipped, skipped_large


def run_ruff(files, chunk=400, select=("E9", "F"), isolated=True):
    """E9 = syntax/runtime errors, F = pyflakes (imports, undefined, redefined).
    These are the 'actually broken' rules; style noise (UP/B/D/I/S) is excluded
    unless the user passes --all. --isolated ignores per-project ruff configs
    so the result is comparable across the whole tree."""
    base = [RUFF, "check", "--output-format=json", "--no-cache",
            "--quiet", "--force-exclude"]
    if isolated:
        base.append("--isolated")
    if select:
        base.append("--select=" + ",".join(select))
    all_issues = []
    for i in range(0, len(files), chunk):
        batch = files[i:i + chunk]
        try:
            r = subprocess.run(
                base + batch,
                capture_output=True, text=True, cwd=os.path.expanduser("~"),
            )
        except FileNotFoundError:
            print(f"ERROR: ruff not found at {RUFF}. "
                  "pip install ruff or fix RUFF path in this script.", file=sys.stderr)
            sys.exit(2)
        if r.stdout:
            try:
                all_issues.extend(json.loads(r.stdout))
            except json.JSONDecodeError:
                pass
        # ruff returns non-zero when issues found — that's expected, not an error.
    return all_issues


def category_for(code: str) -> str:
    if code == "E999":
        return "SyntaxError"
    if code.startswith("F4"):
        return "import-issue"
    if code.startswith("F8"):
        return "undefined/redefined"
    if code.startswith("E9"):
        return "runtime-syntax"
    if code.startswith("E7"):
        return "style-comparison"
    return code[0] + " (other)"


def build_report(issues, total_files, skipped, root, started):
    by_cat = Counter()
    by_code = Counter()
    files_with = defaultdict(list)
    for it in issues:
        code = it.get("code", "?")
        cat = category_for(code)
        by_cat[cat] += 1
        by_code[code] += 1
        fname = it.get("filename", "?")
        try:
            fname = os.path.relpath(fname, str(root))
        except ValueError:
            pass
        files_with[fname].append(it)

    broken_files = len(files_with)
    lines = []
    lines.append(f"# Triage Report — {started:%Y-%m-%d %H:%M}")
    lines.append("")
    lines.append(f"- Root: `{root}`")
    lines.append(f"- Scanned: {total_files} .py files ({skipped} excluded as venv/cache/archive)")
    lines.append(f"- Broken files: {broken_files} ({100*broken_files/max(total_files,1):.1f}%)")
    lines.append(f"- Total issues: {len(issues)}")
    lines.append("")
    lines.append("## By category")
    lines.append("| Category | Issues | Files |")
    lines.append("|---|---:|---:|")
    cat_files = Counter()
    for it in issues:
        cat_files[category_for(it.get("code","?"))]  # touch
    # files-per-category
    cat_to_files = defaultdict(set)
    for fname, its in files_with.items():
        for it in its:
            cat_to_files[category_for(it.get("code","?"))].add(fname)
    for cat, n in by_cat.most_common():
        lines.append(f"| {cat} | {n} | {len(cat_to_files[cat])} |")
    lines.append("")
    lines.append("## By rule code")
    lines.append("| Code | Name | Count |")
    lines.append("|---|---|---:|")
    for code, n in by_code.most_common(25):
        name = RUFF_RULE_NAMES.get(code, "?")
        lines.append(f"| {code} | {name} | {n} |")
    lines.append("")
    lines.append("## Worst offenders (files by issue count)")
    for fname, its in sorted(files_with.items(), key=lambda kv: -len(kv[1]))[:30]:
        lines.append(f"- `{fname}` — {len(its)} issues")
    lines.append("")
    # Syntax errors first — fix these before anything else.
    syntax = [it for it in issues if it.get("code") == "E999"]
    if syntax:
        lines.append(f"## SyntaxErrors ({len(syntax)}) — fix these first")
        for it in syntax[:200]:
            fname = it.get("filename", "?")
            try:
                fname = os.path.relpath(fname, str(root))
            except ValueError:
                pass
            loc = it.get("location", {})
            row = loc.get("row", "?")
            col = loc.get("column", "?")
            msg = it.get("message", "?")
            lines.append(f"- `{fname}:{row}:{col}` — {msg}")
        if len(syntax) > 200:
            lines.append(f"- … and {len(syntax)-200} more (see full report JSON)")
    lines.append("")
    return "\n".join(lines), by_cat, by_code, broken_files


def main():
    ap = argparse.ArgumentParser(description="Static error-map scanner.")
    ap.add_argument("root", nargs="?", default="~", help="root dir to scan (default ~)")
    ap.add_argument("--out", default=os.path.expanduser("~/triage_report.md"),
                    help="markdown report path")
    ap.add_argument("--include-unpacked", action="store_true",
                    help="don't skip UNPACKED/ archives")
    ap.add_argument("--all", action="store_true",
                    help="also include style rules (UP/B/D/I/S) — noisy; default is breakage-only")
    args = ap.parse_args()

    root = Path(os.path.expanduser(args.root)).resolve()
    if not root.is_dir():
        print(f"not a dir: {root}", file=sys.stderr); sys.exit(2)

    if args.include_unpacked:
        EXCLUDE_DIR_PARTS.discard("UNPACKED")

    started = datetime.now()
    print(f"triage: collecting .py files under {root} …", file=sys.stderr)
    files, skipped, skipped_large = collect_py_files(root)
    print(f"triage: {len(files)} files to check ({skipped} excluded, "
          f"{skipped_large} too large). "
          f"running ruff in chunks…", file=sys.stderr)

    if args.all:
        issues = run_ruff(files, select=(), isolated=False)
    else:
        issues = run_ruff(files)
    report, by_cat, by_code, broken = build_report(issues, len(files), skipped, root, started)

    Path(args.out).write_text(report)
    # also drop machine-readable JSONL beside it
    jsonl = args.out.replace(".md", ".jsonl")
    with open(jsonl, "w") as fh:
        for it in issues:
            fh.write(json.dumps(it) + "\n")

    # stdout summary (terse)
    print(f"\n=== Triage summary ===")
    print(f"scanned {len(files)} files ({skipped} excluded) under {root}")
    print(f"broken files: {broken}   total issues: {len(issues)}")
    print(f"\ntop categories:")
    for cat, n in by_cat.most_common(8):
        print(f"  {n:6d}  {cat}")
    print(f"\ntop rule codes:")
    for code, n in by_code.most_common(8):
        print(f"  {n:6d}  {code} ({RUFF_RULE_NAMES.get(code,'?')})")
    print(f"\nfull report: {args.out}")
    print(f"jsonl:       {jsonl}")


if __name__ == "__main__":
    main()
