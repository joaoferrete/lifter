"""Persistent coach memories — facts extracted from chat conversations."""
import sqlite3
from config import DB_PATH


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def save_memory(summary: str, category: str = "general") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO chat_memories (created_at, summary, category) VALUES (datetime('now'), ?, ?)",
            (summary, category),
        )


def get_recent_memories(limit: int = 15) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_memories ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def clear_memories() -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM chat_memories")


def memories_as_context(limit: int = 15) -> str:
    memories = get_recent_memories(limit)
    if not memories:
        return ""
    from ai.sanitize import sanitize_for_prompt
    lines = ["## Coach memory (from previous conversations)"]
    for m in memories:
        date = (m.get("created_at") or "")[:10]
        safe_summary = sanitize_for_prompt(m["summary"], max_len=300)
        lines.append(f"  - [{date}] {safe_summary}")
    return "\n".join(lines)
