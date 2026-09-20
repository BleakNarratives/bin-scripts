#!/usr/bin/env python3

# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: workspace-hygiene
# DEPS: hashlib,os,pathlib
# ROLE: Duplicate file scanner for the Crostini ecosystem
# AUTHOR: Bleak
# SESSION: 2026-09-11
# TIER: 4
# [/DNA_TAG]

"""
Duplicate file scanner for the Crostini ecosystem.

Scans Python (and optionally all) files across project directories,
finds exact duplicates by content hash, and generates a report showing:
  - Which files are duplicated and where
  - Which projects have the most internal duplication
  - Cross-project duplicate groups
  - Wasted disk space
  - Nested mirror directories (same dir duplicated at different depths)

Usage:
  python3 ~/bin/dedup_scanner.py                    # scan Python files only
  python3 ~/bin/dedup_scanner.py --all              # scan all file types
  python3 ~/bin/dedup_scanner.py --report /tmp/dedup_report.md  # write report file
  python3 ~/bin/dedup_scanner.py --min-size 1024    # skip files < 1KB
  python3 ~/bin/dedup_scanner.py --since 30         # only files modified in last 30 days

DNA_TAG: ORIGIN=BleakNarratives/MikeySwarm | PILLAR=hygiene | ROLE=dedup-scanner | LAST_SYNC=2026-09-01
"""

import argparse
import hashlib
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()

# Directories that are canonical project roots (the source of truth).
# Everything else is either a recovery snapshot, a venv, or cache.
CANONICAL_PROJECTS = [
    "OutcLaw",
    "Code-City-Apocalypse",
    "SyntaxIntelligence",
    "Whorl",
    "RootBase",
    "MikeySwarm",
    "Official-Vertical-AI-Boardroom",
    "passive_income_swarm",
    "Implore",
    "Skin-Deep",
    "payment_system",
    "sprint-ralphy",
    "MemGuard",
    "NatHub",
    "Merkaba",
    "Pytch",
    "Praudit",
    "PivotCity",
    "Poncho-Villa",
    "Negotiator",
    "Anchor",
    "pattern_journal",
    "propagated_node",
    "cloud_bridge",
    "thoth_orchestrator",
    "Kindred",
    "TruthSleuth",
    "feral-swarm",
    "HiveMind",
    "GlintEngine",
    "Akasha",
    "Bardildo",
    "CodeMentor",
]

# Directories to always skip (recovery snapshots, caches, toolchains, vendored)
SKIP_DIRS = {
    # Caches & toolchains
    ".venv", "venv", "__pycache__", "site-packages", "node_modules",
    ".git", ".cache", ".npm", ".npm-global", ".cargo", ".rustup",
    ".hermit", ".ruff_cache", ".dspy_cache", ".gnupg",
    # Agent/skill caches (not our code)
    ".agents", ".codex", ".claude", ".gemini", ".copilot",
    ".jules", ".hermes", ".grimoire", ".akasha", ".orchestra",
    # Recovery / staging (not canonical)
    "NAT-Recovery-Kernel", "UNPACKED", "recovery_staged",
    "._archive", "The-Werkz", "RootBase_recovery",
    "SCOUT_CONTAINER", "n_recovery", "p_recovery",
    "Anchor_from_zip", "syntax_shipwrekd_os_bundle",
    "BleakNarratives_loosies_tar_zip", "Download_021116",
    "GRAVEYARD", "rag_seal_sim_dupe", "root_2026",
    "loosies_mel_tab_085619", ".SLaSHdotHIDEfiles",
    "Downloads", "Documents", "Documents_but_not_kinda",
    # Misc
    "berd", "buzz", "llama.cpp", "llamacpp", "llama_native",
    "models", "sdk", "opt", "etc", "mnt", "cloud_mount",
}


def should_skip(path: Path) -> bool:
    """Check if a path component matches any skip directory."""
    for part in path.parts:
        if part in SKIP_DIRS:
            return True
    # Also skip hidden dotfiles that aren't project dirs
    return False


def get_project(path: Path) -> str:
    """Extract the top-level project name from a path under HOME."""
    try:
        rel = path.relative_to(HOME)
        return rel.parts[0] if rel.parts else "unknown"
    except ValueError:
        return "unknown"


def hash_file(filepath: Path, min_size: int = 0) -> str | None:
    """Return md5 hash of file content, or None if file is too small / unreadable."""
    try:
        size = filepath.stat().st_size
        if size <= min_size:
            return None
        # For files > 100 bytes, use full hash. For tiny files, still hash.
        with open(filepath, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except (OSError, PermissionError):
        return None


def scan_files(extensions: list[str] | None, min_size: int, since_days: int | None) -> list[Path]:
    """Walk HOME and collect files matching the extension filter."""
    cutoff = None
    if since_days:
        cutoff = time.time() - (since_days * 86400)

    files = []
    for root, dirs, filenames in os.walk(HOME):
        root_path = Path(root)

        # Prune skip dirs from traversal
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".env_vault")]

        # Also skip symlinked dirs (we already handled the symlink)
        dirs[:] = [d for d in dirs if not (root_path / d).is_symlink()]

        for fname in filenames:
            fpath = root_path / fname

            # Extension filter
            if extensions:
                if not any(fname.endswith(ext) for ext in extensions):
                    continue

            # Size filter
            try:
                if fpath.stat().st_size <= min_size:
                    continue
            except OSError:
                continue

            # Time filter
            if cutoff:
                try:
                    if fpath.stat().st_mtime < cutoff:
                        continue
                except OSError:
                    continue

            files.append(fpath)

    return files


def find_duplicates(files: list[Path]) -> dict[str, list[Path]]:
    """Hash all files and group by content hash. Return only groups with >1 copy."""
    groups: dict[str, list[Path]] = defaultdict(list)

    for i, fpath in enumerate(files):
        h = hash_file(fpath)
        if h:
            groups[h].append(fpath)
        if (i + 1) % 500 == 0:
            print(f"  Hashed {i+1}/{len(files)} files...", file=sys.stderr)

    # Return only duplicates
    return {h: paths for h, paths in groups.items() if len(paths) > 1}


def find_nested_mirrors(files: list[Path]) -> list[dict]:
    """Find directories that appear to be nested copies of parent dirs.

    A nested mirror is when a directory contains a subdirectory with the same name
    and similar file structure (e.g., truthsleuth/truthsleuth/).
    """
    mirrors = []

    # Group files by their immediate parent directory name
    dir_files: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        dir_files[f.parent.name].append(f)

    # Find cases where a dir name appears nested inside itself
    for name, file_list in dir_files.items():
        if len(file_list) < 3:
            continue
        # Check if any file's path contains the parent name more than once
        for f in file_list:
            parts = f.parts
            name_count = sum(1 for p in parts if p == name)
            if name_count > 1:
                mirrors.append({
                    "dir_name": name,
                    "path": str(f.parent),
                    "file_count": len(file_list),
                })
                break

    return mirrors


def generate_report(
    files: list[Path],
    duplicates: dict[str, list[Path]],
    nested_mirrors: list[dict],
    extensions: list[str] | None,
    min_size: int,
    since_days: int | None,
) -> str:
    """Generate a markdown report."""
    total_files = len(files)
    unique_files = len({hash_file(f) for f in files if hash_file(f)})
    dup_file_count = sum(len(paths) for paths in duplicates.values())
    redundant_copies = dup_file_count - len(duplicates)

    # Calculate wasted space
    total_waste = 0
    for paths in duplicates.values():
        try:
            sz = paths[0].stat().st_size
            total_waste += sz * (len(paths) - 1)
        except OSError:
            pass

    # Files per project
    project_counts: dict[str, int] = defaultdict(int)
    for f in files:
        project_counts[get_project(f)] += 1

    # Duplicates per project
    dup_project_counts: dict[str, int] = defaultdict(int)
    for paths in duplicates.values():
        for p in paths:
            dup_project_counts[get_project(p)] += 1

    # Cross-project duplicates
    cross_project = 0
    for paths in duplicates.values():
        projects = {get_project(p) for p in paths}
        if len(projects) > 1:
            cross_project += 1

    ext_label = ", ".join(extensions) if extensions else "all types"

    lines = []
    lines.append("# Dedup Scanner Report")
    lines.append(f"\nGenerated: {datetime.now().isoformat()}")
    lines.append(f"\n## Summary\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| File types scanned | {ext_label} |")
    lines.append(f"| Total files indexed | {total_files:,} |")
    lines.append(f"| Unique files (by content) | {unique_files:,} |")
    lines.append(f"| Duplicate groups | {len(duplicates):,} |")
    lines.append(f"| Files in duplicate groups | {dup_file_count:,} |")
    lines.append(f"| Redundant copies | {redundant_copies:,} |")
    lines.append(f"| Cross-project duplicate groups | {cross_project:,} |")
    lines.append(f"| Wasted disk space | {total_waste / 1048576:.1f} MB |")
    if since_days:
        lines.append(f"| Time window | last {since_days} days |")
    if min_size > 0:
        lines.append(f"| Min file size | {min_size} bytes |")

    # Top duplicated files
    lines.append(f"\n## Top 20 Duplicate Groups (by copy count)\n")
    sorted_dups = sorted(duplicates.items(), key=lambda x: len(x[1]), reverse=True)
    lines.append(f"| # Copies | Project(s) | File | Size |")
    lines.append(f"|----------|-----------|------|------|")
    for h, paths in sorted_dups[:20]:
        try:
            sz = paths[0].stat().st_size
            size_str = f"{sz:,} B" if sz < 1024 else f"{sz / 1024:.1f} KB"
        except OSError:
            size_str = "?"
        projects = sorted({get_project(p) for p in paths})
        proj_str = ", ".join(projects)
        # Use the shortest filename as representative
        fname = min(paths, key=lambda p: len(str(p))).name
        lines.append(f"| {len(paths)} | {proj_str} | {fname} | {size_str} |")

    # Files per project
    lines.append(f"\n## Files Per Project\n")
    lines.append(f"| Project | Total Files | In Duplicate Groups |")
    lines.append(f"|---------|-------------|---------------------|")
    for proj in sorted(project_counts, key=project_counts.get, reverse=True)[:25]:
        total = project_counts[proj]
        in_dups = dup_project_counts.get(proj, 0)
        pct = f"{in_dups / total * 100:.0f}%" if total > 0 else "0%"
        lines.append(f"| {proj} | {total} | {in_dups} ({pct}) |")

    # Nested mirrors
    if nested_mirrors:
        lines.append(f"\n## Nested Mirror Directories (possible self-copies)\n")
        lines.append(f"| Directory Name | Path | File Count |")
        lines.append(f"|----------------|------|------------|")
        for m in nested_mirrors:
            lines.append(f"| {m['dir_name']} | {m['path']} | {m['file_count']} |")

    # Actionable recommendations
    lines.append(f"\n## Recommended Actions\n")
    if redundant_copies > 0:
        lines.append(f"1. **Delete redundant copies**: {redundant_copies} files are exact duplicates — pick one canonical location per group and delete the rest.")
    if cross_project > 0:
        lines.append(f"2. **Cross-project duplication**: {cross_project} file groups exist across multiple projects — extract shared code into a common library or symlink.")
    if nested_mirrors:
        lines.append(f"3. **Flatten nested mirrors**: {len(nested_mirrors)} directories appear to be self-nested copies (e.g., `foo/foo/`). Flatten to one level.")
    if not (redundant_copies or cross_project or nested_mirrors):
        lines.append("No duplication issues found. Codebase is clean.")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Scan for duplicate files across the Crostini ecosystem."
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Scan all file types (default: Python only)"
    )
    parser.add_argument(
        "--report", type=str, default=None,
        help="Write report to this file (default: stdout)"
    )
    parser.add_argument(
        "--min-size", type=int, default=100,
        help="Skip files smaller than this many bytes (default: 100)"
    )
    parser.add_argument(
        "--since", type=int, default=None,
        help="Only scan files modified in the last N days"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output duplicate groups as JSON instead of markdown report"
    )
    args = parser.parse_args()

    extensions = None if args.all else [".py"]

    print(f"Scanning ~ for {'all files' if args.all else 'Python files'}...", file=sys.stderr)
    if args.since:
        print(f"  (only files modified in last {args.since} days)", file=sys.stderr)

    files = scan_files(extensions, args.min_size, args.since)
    print(f"Found {len(files):,} files. Hashing...", file=sys.stderr)

    duplicates = find_duplicates(files)
    print(f"Found {len(duplicates):,} duplicate groups.", file=sys.stderr)

    nested_mirrors = find_nested_mirrors(files)

    if args.json:
        import json
        output = {
            "summary": {
                "total_files": len(files),
                "duplicate_groups": len(duplicates),
                "redundant_copies": sum(len(p) - 1 for p in duplicates.values()),
            },
            "duplicates": [
                {"hash": h, "paths": [str(p) for p in paths]}
                for h, paths in sorted(duplicates.items(), key=lambda x: len(x[1]), reverse=True)
            ],
            "nested_mirrors": nested_mirrors,
        }
        report = json.dumps(output, indent=2)
    else:
        report = generate_report(files, duplicates, nested_mirrors, extensions, args.min_size, args.since)

    if args.report:
        Path(args.report).write_text(report)
        print(f"\nReport written to {args.report}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
