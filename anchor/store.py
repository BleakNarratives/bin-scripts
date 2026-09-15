# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: armory
# DEPS: sqlite3 (stdlib)
# ROLE: Anchor's persistent store — history, facts, tool runs
# AUTHOR: Bleak
# SESSION: 2026-09-15
# TIER: 3
# [/DNA_TAG]

"""Store — Anchor's persistent state.

Anchor used to keep conversation history in a module-level list, so every run
started from nothing and nothing it learned survived a single invocation. This
is the database it needed: stdlib sqlite3, one file, no server.

Location: ~/.anchor/anchor.db
Tables:   messages (history), facts (key/value memory), tool_runs (audit)
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DB_PATH = Path.home() / ".anchor" / "anchor.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    role    TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
CREATE TABLE IF NOT EXISTS facts (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    ts    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_runs (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL NOT NULL,
    tool   TEXT NOT NULL,
    args   TEXT NOT NULL,
    ok     INTEGER NOT NULL,
    output TEXT
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.executescript(_SCHEMA)
    return conn


# ── conversation history ─────────────────────────────────────────────────────

def push(role: str, content: str, path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.execute("INSERT INTO messages (ts, role, content) VALUES (?,?,?)",
                     (time.time(), role, content or ""))


def get_recent(n: int = 6, path: Path | None = None) -> list[dict]:
    """Most recent messages, oldest first (the order a prompt wants them)."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


def search_messages(query: str, limit: int = 20,
                    path: Path | None = None) -> list[dict]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT ts, role, content FROM messages WHERE content LIKE ? "
            "ORDER BY id DESC LIMIT ?", (f"%{query}%", limit)
        ).fetchall()
    return [{"ts": t, "role": r, "content": c} for t, r, c in rows]


# ── durable facts ────────────────────────────────────────────────────────────

def remember(key: str, value: str, path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO facts (key, value, ts) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
            (key, value, time.time()))


def recall(key: str, path: Path | None = None) -> str | None:
    with connect(path) as conn:
        row = conn.execute("SELECT value FROM facts WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def all_facts(path: Path | None = None) -> list[dict]:
    with connect(path) as conn:
        rows = conn.execute("SELECT key, value, ts FROM facts ORDER BY key").fetchall()
    return [{"key": k, "value": v, "ts": t} for k, v, t in rows]


# ── tool audit ───────────────────────────────────────────────────────────────

def log_tool(tool: str, args: str, ok: bool, output: str,
             path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO tool_runs (ts, tool, args, ok, output) VALUES (?,?,?,?,?)",
            (time.time(), tool, str(args)[:2000], 1 if ok else 0,
             (output or "")[:4000]))


def recent_tool_runs(limit: int = 20, path: Path | None = None) -> list[dict]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT ts, tool, args, ok, output FROM tool_runs ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
    return [{"ts": t, "tool": tool, "args": a, "ok": bool(ok), "output": o}
            for t, tool, a, ok, o in rows]


def stats(path: Path | None = None) -> dict:
    with connect(path) as conn:
        msg = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        facts = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0]
    return {"messages": msg, "facts": facts, "tool_runs": runs,
            "db": str(path or DB_PATH)}


if __name__ == "__main__":
    print(stats())
