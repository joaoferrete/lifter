"""render_cache — memoization keyed by (active DB, key) with explicit invalidation."""

import config
import render_cache


def setup_function() -> None:
    render_cache.invalidate()


def test_cached_computes_once():
    calls = []

    def producer():
        calls.append(1)
        return "value"

    assert render_cache.cached("k", producer) == "value"
    assert render_cache.cached("k", producer) == "value"
    assert len(calls) == 1


def test_invalidate_forces_recompute():
    calls = []

    def producer():
        calls.append(1)
        return len(calls)

    assert render_cache.cached("k", producer) == 1
    render_cache.invalidate()
    assert render_cache.cached("k", producer) == 2


def test_entries_namespaced_by_db_path(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "a.db")
    assert render_cache.cached("k", lambda: "from-a") == "from-a"

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "b.db")
    assert render_cache.cached("k", lambda: "from-b") == "from-b"

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "a.db")
    assert render_cache.cached("k", lambda: "never-called") == "from-a"


def test_distinct_keys_do_not_collide():
    assert render_cache.cached("k1", lambda: 1) == 1
    assert render_cache.cached("k2", lambda: 2) == 2
