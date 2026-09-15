# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: armory
# DEPS: store, sqlite3
# ROLE: Persistent conversation memory (was an in-memory list)
# AUTHOR: Bleak
# SESSION: 2026-09-15
# TIER: 3
# [/DNA_TAG]

"""memory — Anchor's history, now durable.

This used to be a module-level `_history = []`, which meant every Anchor run
started blank and nothing survived the process. It now delegates to the SQLite
store so history and facts persist across runs.

The public functions (get_recent, push) are unchanged on purpose: anchor.py
already calls them, and the point of this change is durability, not a new API.
"""

from __future__ import annotations

import store


def get_recent(n: int = 6) -> list[dict]:
    return store.get_recent(n)


def push(role: str, content: str) -> None:
    store.push(role, content)


def remember(key: str, value: str) -> None:
    store.remember(key, value)


def recall(key: str) -> str | None:
    return store.recall(key)


def stats() -> dict:
    return store.stats()
