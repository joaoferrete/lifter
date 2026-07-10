"""Regression tests: every DB helper must CLOSE its connection.

sqlite3's connection context manager only commits/rolls back — before the
`transaction()` wrapper was introduced, every call leaked one open handle.
"""

import sqlite3
from typing import ClassVar


class _TrackingConnection(sqlite3.Connection):
    instances: ClassVar[list["_TrackingConnection"]] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.closed = False
        _TrackingConnection.instances.append(self)

    def close(self):
        self.closed = True
        super().close()


def _tracking_factory(db_path):
    def factory(path=None):
        conn = sqlite3.connect(str(db_path), factory=_TrackingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    return factory


def test_store_helpers_close_connections(tmp_db, monkeypatch):
    import db.store as store_mod

    _TrackingConnection.instances.clear()
    monkeypatch.setattr(store_mod, "_conn", _tracking_factory(tmp_db))

    store_mod.set_sync_state("k", "v", db_path=tmp_db)
    store_mod.get_sync_state("k", db_path=tmp_db)
    store_mod.query("SELECT 1 AS one", db_path=tmp_db)

    assert len(_TrackingConnection.instances) == 3
    assert all(c.closed for c in _TrackingConnection.instances)


def test_goals_helpers_close_connections(tmp_db, monkeypatch):
    import db.goals as goals_mod

    _TrackingConnection.instances.clear()
    monkeypatch.setattr(goals_mod, "_conn", _tracking_factory(tmp_db))

    goals_mod.set_pref("some_key", "1")
    goals_mod.get_pref("some_key")
    goals_mod.get_goals()

    assert len(_TrackingConnection.instances) == 3
    assert all(c.closed for c in _TrackingConnection.instances)


def test_memories_helpers_close_connections(tmp_db, monkeypatch):
    import db.memories as mem_mod

    _TrackingConnection.instances.clear()
    monkeypatch.setattr(mem_mod, "_conn", _tracking_factory(tmp_db))

    mem_mod.save_memory("remember this")
    mem_mod.count_memories()

    assert len(_TrackingConnection.instances) >= 2
    assert all(c.closed for c in _TrackingConnection.instances)


def test_transaction_closes_on_error(tmp_db):
    from db.store import transaction

    conn = sqlite3.connect(str(tmp_db), factory=_TrackingConnection)
    try:
        with transaction(conn):
            raise ValueError("boom")
    except ValueError:
        pass
    assert conn.closed


def test_transaction_rolls_back_on_error(tmp_db):
    import db.store as store_mod

    store_mod.init_db(tmp_db)
    conn = store_mod.connect(tmp_db)
    try:
        with store_mod.transaction(conn) as c:
            c.execute("INSERT INTO sync_state (key, value) VALUES ('a', '1')")
            raise ValueError("boom")
    except ValueError:
        pass
    assert store_mod.get_sync_state("a", db_path=tmp_db) is None
