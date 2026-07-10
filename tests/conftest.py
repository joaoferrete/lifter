"""Shared test fixtures — isolate every test to its own in-memory SQLite database."""

import importlib
import os
import sqlite3
import tempfile
from datetime import UTC
from pathlib import Path

import pytest

# Sandbox all writable paths (paths.py module constants) before any project
# module is imported — keeps the suite away from the user's real XDG dirs.
os.environ.setdefault("LIFTER_HOME", tempfile.mkdtemp(prefix="lifter-tests-"))


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def tmp_db(db_path, monkeypatch) -> Path:
    """
    Fully-isolated SQLite database per test.

    Patches every module that touches the DB so tests never read from or
    write to the real hevy.db on disk.
    """

    def _make_conn():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ── db.store ──────────────────────────────────────────────────────────────
    import config
    import db.store as store_mod

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(store_mod, "_conn", lambda p=db_path: _make_conn())

    _orig_query = store_mod.query.__wrapped__ if hasattr(store_mod.query, "__wrapped__") else store_mod.query

    def _query(sql, params=(), **_kw):
        return _orig_query(sql, params, db_path=db_path)

    monkeypatch.setattr(store_mod, "query", _query)

    # Patch every write helper to default to the test DB
    for fn_name in (
        "upsert_workout",
        "delete_workout",
        "upsert_exercise_template",
        "upsert_body_measurement",
        "upsert_routine",
        "delete_stale_routines",
        "get_routines_with_exercises",
        "get_sync_state",
        "set_sync_state",
    ):
        _orig = getattr(store_mod, fn_name)

        def _make_patched(original):
            def _patched(*a, **kw):
                kw.setdefault("db_path", db_path)
                return original(*a, **kw)

            return _patched

        monkeypatch.setattr(store_mod, fn_name, _make_patched(_orig))

    # ── analytics modules ────────────────────────────────────────────────────
    for mod_name in (
        "analytics.volume",
        "analytics.progression",
        "analytics.frequency",
        "analytics.records",
        "analytics.common",
    ):
        mod = importlib.import_module(mod_name)
        monkeypatch.setattr(mod, "query", _query)

    # ── db.goals / db.memories ───────────────────────────────────────────────
    import db.goals as goals_mod

    monkeypatch.setattr(goals_mod, "_conn", _make_conn)

    import db.memories as mem_mod

    monkeypatch.setattr(mem_mod, "_conn", _make_conn)

    # ── fit.sync ─────────────────────────────────────────────────────────────
    try:
        import fit.sync as fit_sync_mod

        monkeypatch.setattr(fit_sync_mod, "_conn", _make_conn)
    except Exception:
        pass

    # ── fit.analytics ─────────────────────────────────────────────────────────
    try:
        import fit.analytics as fit_analytics_mod

        monkeypatch.setattr(fit_analytics_mod, "query", _query)
    except Exception:
        pass

    # ── ai.coach ─────────────────────────────────────────────────────────────
    try:
        import ai.coach as coach_mod

        monkeypatch.setattr(coach_mod, "query", _query)
        _orig_grwe = coach_mod.get_routines_with_exercises
        monkeypatch.setattr(coach_mod, "get_routines_with_exercises", lambda: _orig_grwe(db_path=db_path))
    except Exception:
        pass

    # Initialise schema in the test DB
    store_mod.init_db(db_path)
    return db_path


# ── helpers used by multiple test modules ─────────────────────────────────────

TEMPLATE_ID = "TMPL001"
WORKOUT_ID_BASE = "wk-"


def seed_exercise_template(db_path, template_id=TEMPLATE_ID, muscle="chest"):
    from db.store import upsert_exercise_template

    upsert_exercise_template(
        {
            "id": template_id,
            "title": f"Exercise {template_id}",
            "type": "weight_reps",
            "primary_muscle_group": muscle,
            "secondary_muscle_groups": [],
            "is_custom": False,
        },
        db_path=db_path,
    )


def seed_routine(db_path, routine_id, title="Test Routine", template_id=TEMPLATE_ID):
    from db.store import upsert_routine

    upsert_routine(
        {
            "id": routine_id,
            "title": title,
            "notes": None,
            "folder_id": None,
            "updated_at": "2024-01-01T00:00:00Z",
            "created_at": "2024-01-01T00:00:00Z",
            "exercises": [
                {
                    "exercise_template_id": template_id,
                    "title": f"Exercise {template_id}",
                    "notes": None,
                    "rest_seconds": 90,
                    "index": 0,
                    "superset_id": None,
                    "sets": [
                        {"index": 0, "type": "normal", "weight_kg": 80.0, "reps": 8},
                        {"index": 1, "type": "normal", "weight_kg": 80.0, "reps": 8},
                    ],
                }
            ],
        },
        db_path=db_path,
    )


def seed_workout(db_path, workout_id, template_id=TEMPLATE_ID, days_ago=0, sets=None):
    from datetime import datetime, timedelta

    from db.store import upsert_workout

    start = datetime.now(UTC) - timedelta(days=days_ago)
    end = start + timedelta(hours=1)
    sets = sets or [{"index": 0, "type": "normal", "weight_kg": 80.0, "reps": 5}]

    upsert_workout(
        {
            "id": workout_id,
            "title": f"Workout {workout_id}",
            "description": None,
            "routine_id": None,
            "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updated_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "created_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "exercises": [
                {
                    "exercise_template_id": template_id,
                    "title": f"Exercise {template_id}",
                    "index": 0,
                    "supersets_id": None,
                    "notes": None,
                    "sets": sets,
                }
            ],
        },
        db_path=db_path,
    )
