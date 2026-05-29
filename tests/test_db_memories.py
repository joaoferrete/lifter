"""Tests for the chat memories persistence layer."""


def test_save_and_retrieve_memory(tmp_db):
    from db.memories import save_memory, get_recent_memories
    save_memory("User prefers morning workouts.", category="preference")
    memories = get_recent_memories()
    assert len(memories) == 1
    assert memories[0]["summary"] == "User prefers morning workouts."
    assert memories[0]["category"] == "preference"


def test_most_recent_first(tmp_db):
    from db.memories import get_recent_memories, _conn

    # Insert with explicit timestamps to guarantee ordering
    with _conn() as conn:
        conn.execute("INSERT INTO chat_memories (created_at, summary) VALUES ('2024-01-01T10:00:00', 'Older memory')")
        conn.execute("INSERT INTO chat_memories (created_at, summary) VALUES ('2024-01-02T10:00:00', 'Newer memory')")

    memories = get_recent_memories()
    assert memories[0]["summary"] == "Newer memory"


def test_limit_respected(tmp_db):
    from db.memories import save_memory, get_recent_memories
    for i in range(20):
        save_memory(f"Memory {i}")
    assert len(get_recent_memories(limit=5)) == 5
    assert len(get_recent_memories(limit=15)) == 15


def test_clear_memories(tmp_db):
    from db.memories import save_memory, get_recent_memories, clear_memories
    save_memory("Will be cleared")
    clear_memories()
    assert get_recent_memories() == []


def test_memories_as_context_empty(tmp_db):
    from db.memories import memories_as_context
    assert memories_as_context() == ""


def test_memories_as_context_formats_correctly(tmp_db):
    from db.memories import save_memory, memories_as_context
    save_memory("Left knee pain when squatting deep.", category="injury")
    ctx = memories_as_context()
    assert "Coach memory" in ctx
    assert "Left knee pain" in ctx
