# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: armory
# DEPS: stdlib only
# ROLE: Codebase search and exact-match patching for Anchor
# AUTHOR: Bleak
# SESSION: 2026-09-15
# TIER: 3
# [/DNA_TAG]

"""patcher — the module anchor.py imported but that never existed.

anchor.py imported `patcher` for `--search` and `--patch`; the file was missing,
so `import anchor` raised ModuleNotFoundError and the whole CLI was dead.

apply_patch is deliberately conservative: it only writes when the old text
matches exactly once, and it leaves a .bak first. A patch that is ambiguous is
refused, not guessed at.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv",
             "site-packages", ".cache", ".rustup", ".cargo"}
MAX_FILE_BYTES = 2_000_000


def search_codebase(root: str, pattern: str, glob: str = "*.py",
                    limit: int = 50, regex: bool = False) -> str:
    """Grep a tree for a literal string or regex. Returns 'path:line: text'."""
    base = Path(os.path.expanduser(root)).resolve()
    if not base.is_dir():
        return f"[patcher] not a directory: {base}"
    try:
        matcher = re.compile(pattern) if regex else None
    except re.error as exc:
        return f"[patcher] bad regex: {exc}"

    hits: list[str] = []
    for current, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if not Path(name).match(glob):
                continue
            path = Path(current) / name
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = path.read_text(errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                found = bool(matcher.search(line)) if matcher else (pattern in line)
                if found:
                    hits.append(f"{path}:{lineno}: {line.strip()[:200]}")
                    if len(hits) >= limit:
                        return "\n".join(hits) + f"\n[patcher] capped at {limit}"
    return "\n".join(hits) if hits else f"[patcher] no matches for {pattern!r}"


def apply_patch(path: str, old: str, new: str) -> str:
    """Replace `old` with `new` in a file, only if `old` occurs exactly once."""
    target = Path(os.path.expanduser(path))
    if not target.is_file():
        return f"[patcher] no such file: {target}"
    try:
        original = target.read_text()
    except OSError as exc:
        return f"[patcher] unreadable: {exc}"

    count = original.count(old)
    if count == 0:
        return "[patcher] refused: old text does not appear in the file"
    if count > 1:
        return f"[patcher] refused: old text appears {count} times (ambiguous)"

    backup = target.with_suffix(target.suffix + ".bak")
    backup.write_text(original)
    target.write_text(original.replace(old, new, 1))
    return f"[patcher] patched {target} (backup: {backup.name})"
