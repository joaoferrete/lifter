"""Tests for the chat memories persistence layer."""


def test_save_and_retrieve_memory(tmp_db):
    from db.memories import get_recent_memories, save_memory

    save_memory("User prefers morning workouts.", category="preference")
    memories = get_recent_memories()
    assert len(memories) == 1
    assert memories[0]["summary"] == "User prefers morning workouts."
    assert memories[0]["category"] == "preference"


def test_most_recent_first(tmp_db):
    from db.memories import _conn, get_recent_memories

    # Insert with explicit timestamps to guarantee ordering
    with _conn() as conn:
        conn.execute("INSERT INTO chat_memories (created_at, summary) VALUES ('2024-01-01T10:00:00', 'Older memory')")
        conn.execute("INSERT INTO chat_memories (created_at, summary) VALUES ('2024-01-02T10:00:00', 'Newer memory')")

    memories = get_recent_memories()
    assert memories[0]["summary"] == "Newer memory"


def test_limit_respected(tmp_db):
    from db.memories import get_recent_memories, save_memory

    for i in range(20):
        save_memory(f"Memory {i}")
    assert len(get_recent_memories(limit=5)) == 5
    assert len(get_recent_memories(limit=15)) == 15


def test_clear_memories(tmp_db):
    from db.memories import clear_memories, get_recent_memories, save_memory

    save_memory("Will be cleared")
    clear_memories()
    assert get_recent_memories() == []


def test_memories_as_context_empty(tmp_db):
    from db.memories import memories_as_context

    assert memories_as_context() == ""


def test_memories_as_context_formats_correctly(tmp_db):
    from db.memories import memories_as_context, save_memory

    save_memory("Left knee pain when squatting deep.", category="injury")
    ctx = memories_as_context()
    assert "Coach memory" in ctx
    assert "Left knee pain" in ctx


def test_memory_cap_enforced(tmp_db):
    from db.goals import set_pref
    from db.memories import count_memories, get_all_memories, save_memory

    set_pref("memories_max", "5")
    for i in range(8):
        save_memory(f"Memory {i}")
    assert count_memories() == 5
    kept = {m["summary"] for m in get_all_memories()}
    assert kept == {f"Memory {i}" for i in range(3, 8)}


def test_memory_cap_zero_unlimited(tmp_db):
    from db.goals import set_pref
    from db.memories import count_memories, save_memory

    set_pref("memories_max", "0")
    for i in range(20):
        save_memory(f"Memory {i}")
    assert count_memories() == 20


def test_memory_cap_default(tmp_db):
    from db.memories import _DEFAULT_MEMORIES_MAX, count_memories, save_memory

    for i in range(_DEFAULT_MEMORIES_MAX + 5):
        save_memory(f"Memory {i}")
    assert count_memories() == _DEFAULT_MEMORIES_MAX


def test_enforce_memory_cap_after_lowering_limit(tmp_db):
    from db.goals import set_pref
    from db.memories import count_memories, enforce_memory_cap, save_memory

    for i in range(10):
        save_memory(f"Memory {i}")
    set_pref("memories_max", "3")
    enforce_memory_cap()
    assert count_memories() == 3


def test_delete_memories(tmp_db):
    from db.memories import count_memories, delete_memories, get_all_memories, save_memory

    for i in range(4):
        save_memory(f"Memory {i}")
    ids = [m["id"] for m in get_all_memories()[:2]]
    assert delete_memories(ids) == 2
    assert count_memories() == 2


def test_delete_memories_ignores_unknown_ids(tmp_db):
    from db.memories import count_memories, delete_memories, save_memory

    save_memory("Only one")
    assert delete_memories([9999]) == 0
    assert delete_memories([]) == 0
    assert count_memories() == 1


def test_get_all_memories_newest_first(tmp_db):
    from db.memories import _conn, get_all_memories

    with _conn() as conn:
        conn.execute("INSERT INTO chat_memories (created_at, summary) VALUES ('2024-01-01 10:00:00', 'Older')")
        conn.execute("INSERT INTO chat_memories (created_at, summary) VALUES ('2024-01-02 10:00:00', 'Newer')")
    assert [m["summary"] for m in get_all_memories()] == ["Newer", "Older"]


def test_memories_as_context_not_clipped_at_500(tmp_db):
    from db.memories import memories_as_context, save_memory

    detailed = (
        "User has left shoulder impingement since 2026-06-28 and must avoid overhead "
        "pressing above 40kg. Prefers landmine press and cable lateral raises as "
        "substitutes. Physiotherapist cleared incline dumbbell press up to 30 degrees "
        "as long as reps stay above 8 and the last set stops two reps shy of failure. "
        "Wants to re-test overhead work after the follow-up appointment in August "
        "before adding any vertical pressing volume back into the program."
    )
    assert 300 < len(detailed) <= 500
    save_memory(detailed)
    ctx = memories_as_context()
    assert detailed in ctx
