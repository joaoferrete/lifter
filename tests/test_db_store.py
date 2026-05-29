"""Tests for the SQLite persistence layer."""
import pytest
from tests.conftest import seed_exercise_template, seed_workout, TEMPLATE_ID


def test_init_db_creates_all_tables(tmp_db):
    from db.store import query

    tables = {r["name"] for r in query("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {
        "workouts", "workout_exercises", "workout_sets",
        "exercise_templates", "body_measurements",
        "fit_sleep", "fit_daily",
        "user_goals", "user_preferences", "chat_memories",
        "sync_state",
    }
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"


def test_upsert_and_retrieve_workout(tmp_db):
    from db.store import query
    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "w1")

    rows = query("SELECT * FROM workouts WHERE id = ?", ("w1",))
    assert len(rows) == 1
    assert rows[0]["title"] == "Workout w1"


def test_upsert_workout_cascades_to_sets(tmp_db):
    from db.store import query
    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "w2", sets=[
        {"index": 0, "type": "normal", "weight_kg": 100.0, "reps": 3},
        {"index": 1, "type": "normal", "weight_kg": 90.0, "reps": 5},
    ])

    sets = query("SELECT * FROM workout_sets WHERE workout_id = ?", ("w2",))
    assert len(sets) == 2
    weights = {s["weight_kg"] for s in sets}
    assert weights == {100.0, 90.0}


def test_upsert_workout_replaces_exercises_on_update(tmp_db):
    from db.store import query
    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "w3", sets=[{"index": 0, "type": "normal", "weight_kg": 50.0, "reps": 10}])

    # Re-upsert with different sets
    seed_workout(tmp_db, "w3", sets=[{"index": 0, "type": "normal", "weight_kg": 60.0, "reps": 10}])

    sets = query("SELECT * FROM workout_sets WHERE workout_id = ?", ("w3",))
    assert len(sets) == 1
    assert sets[0]["weight_kg"] == 60.0


def test_delete_workout_cascades(tmp_db):
    from db.store import delete_workout, query
    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "w4")

    delete_workout("w4", db_path=tmp_db)

    assert query("SELECT * FROM workouts WHERE id = ?", ("w4",)) == []
    assert query("SELECT * FROM workout_sets WHERE workout_id = ?", ("w4",)) == []


def test_upsert_exercise_template(tmp_db):
    from db.store import query
    seed_exercise_template(tmp_db, template_id="T99", muscle="biceps")

    rows = query("SELECT * FROM exercise_templates WHERE id = ?", ("T99",))
    assert len(rows) == 1
    assert rows[0]["primary_muscle_group"] == "biceps"


def test_sync_state_roundtrip(tmp_db):
    from db.store import get_sync_state, set_sync_state

    assert get_sync_state("missing_key", db_path=tmp_db) is None
    set_sync_state("last_sync", "2024-01-01T00:00:00Z", db_path=tmp_db)
    assert get_sync_state("last_sync", db_path=tmp_db) == "2024-01-01T00:00:00Z"

    # Overwrite
    set_sync_state("last_sync", "2024-06-01T00:00:00Z", db_path=tmp_db)
    assert get_sync_state("last_sync", db_path=tmp_db) == "2024-06-01T00:00:00Z"


def test_upsert_body_measurement(tmp_db):
    from db.store import upsert_body_measurement, query

    upsert_body_measurement({"date": "2024-01-15", "weight_kg": 80.5, "fat_percent": 18.0}, db_path=tmp_db)
    rows = query("SELECT * FROM body_measurements WHERE date = ?", ("2024-01-15",))
    assert len(rows) == 1
    assert rows[0]["weight_kg"] == 80.5

    # Update
    upsert_body_measurement({"date": "2024-01-15", "weight_kg": 79.0, "fat_percent": 17.5}, db_path=tmp_db)
    rows = query("SELECT * FROM body_measurements WHERE date = ?", ("2024-01-15",))
    assert len(rows) == 1
    assert rows[0]["weight_kg"] == 79.0
