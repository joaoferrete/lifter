"""Persistent coach memories — facts extracted from chat conversations."""

import sqlite3

from db.store import connect as _conn
from db.store import transaction as _tx

_DEFAULT_MEMORIES_MAX = 200
MEMORY_SUMMARY_MAX_LEN = 500


def _memories_max() -> int:
    """Configured cap on stored memories (pref `memories_max`, 0 = unlimited)."""
    try:
        from db.goals import get_pref

        raw = get_pref("memories_max")
        return max(0, int(raw)) if raw is not None else _DEFAULT_MEMORIES_MAX
    except Exception:
        return _DEFAULT_MEMORIES_MAX


def _enforce_cap(conn: sqlite3.Connection) -> None:
    cap = _memories_max()
    if cap:
        conn.execute(
            "DELETE FROM chat_memories WHERE id IN ("
            " SELECT id FROM chat_memories ORDER BY created_at DESC, id DESC"
            " LIMIT -1 OFFSET ?)",
            (cap,),
        )


def save_memory(summary: str, category: str = "general") -> None:
    with _tx(_conn()) as conn:
        conn.execute(
            "INSERT INTO chat_memories (created_at, summary, category) VALUES (datetime('now'), ?, ?)",
            (summary, category),
        )
        _enforce_cap(conn)


def enforce_memory_cap() -> None:
    """Prune to the configured cap now — call after lowering the limit."""
    with _tx(_conn()) as conn:
        _enforce_cap(conn)


def get_recent_memories(limit: int = 15) -> list[dict]:
    with _tx(_conn()) as conn:
        rows = conn.execute("SELECT * FROM chat_memories ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_all_memories() -> list[dict]:
    with _tx(_conn()) as conn:
        rows = conn.execute("SELECT * FROM chat_memories ORDER BY created_at DESC, id DESC").fetchall()
        return [dict(r) for r in rows]


def count_memories() -> int:
    with _tx(_conn()) as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM chat_memories").fetchone()["n"]


def delete_memories(ids: list[int]) -> int:
    """Delete the given memory ids. Returns how many rows were actually removed."""
    if not ids:
        return 0
    with _tx(_conn()) as conn:
        cur = conn.execute(
            f"DELETE FROM chat_memories WHERE id IN ({','.join('?' * len(ids))})",
            list(ids),
        )
        return cur.rowcount


def clear_memories() -> None:
    with _tx(_conn()) as conn:
        conn.execute("DELETE FROM chat_memories")
