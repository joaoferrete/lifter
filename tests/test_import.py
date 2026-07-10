"""Tests for the Developer Settings data import (restore) helper."""

import json

import pytest

import cli
from db.goals import save_goal
from db.memories import count_memories, get_all_memories, save_memory
from db.store import query
from tests.conftest import seed_exercise_template, seed_workout


def test_full_round_trip_restores_data_and_fks(tmp_db, tmp_path):
    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "wk-1", days_ago=1)
    save_goal(type="lift", description="Bench 100kg", target=100, unit="kg")
    save_memory("Prefers dumbbells")

    export_path, _ = cli._export_data("full", dest_dir=tmp_path)

    # mutate: extra memory, workout gone
    save_memory("This one must disappear on restore")
    from db.store import delete_workout

    delete_workout("wk-1", db_path=tmp_db)
    assert query("SELECT COUNT(*) AS n FROM workouts")[0]["n"] == 0

    summary = cli._import_data(export_path)

    assert summary["kind"] == "full"
    assert summary["skipped_tables"] == []
    assert query("SELECT COUNT(*) AS n FROM workouts")[0]["n"] == 1
    assert count_memories() == 1
    assert get_all_memories()[0]["summary"] == "Prefers dumbbells"
    assert query("SELECT description FROM user_goals")[0]["description"] == "Bench 100kg"
    # FK integrity: sets still join to exercises and the workout
    joined = query(
        """SELECT COUNT(*) AS n FROM workout_sets ws
           JOIN workout_exercises we ON we.id = ws.workout_exercise_id
           JOIN workouts w ON w.id = we.workout_id"""
    )[0]["n"]
    assert joined >= 1


def test_partial_import_replaces_only_its_table(tmp_db, tmp_path):
    save_memory("Memory from backup")
    export_path, _ = cli._export_data("memories", dest_dir=tmp_path)

    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "wk-keep", days_ago=1)
    save_memory("Newer memory, will be replaced")

    summary = cli._import_data(export_path)

    assert summary["imported"] == {"chat_memories": 1}
    assert count_memories() == 1
    assert get_all_memories()[0]["summary"] == "Memory from backup"
    assert query("SELECT COUNT(*) AS n FROM workouts")[0]["n"] == 1  # untouched


def test_invalid_payloads_raise(tmp_db, tmp_path):
    wrong_app = tmp_path / "wrong.json"
    wrong_app.write_text(json.dumps({"app": "other", "tables": {}}))
    with pytest.raises(ValueError):
        cli._read_import_payload(wrong_app)

    malformed = tmp_path / "broken.json"
    malformed.write_text("{not json")
    with pytest.raises(ValueError):
        cli._read_import_payload(malformed)

    no_tables = tmp_path / "no_tables.json"
    no_tables.write_text(json.dumps({"app": "lifter", "kind": "full"}))
    with pytest.raises(ValueError):
        cli._read_import_payload(no_tables)


def test_schema_drift_skips_unknown_tables_and_columns(tmp_db, tmp_path):
    payload = {
        "app": "lifter",
        "kind": "memories",
        "tables": {
            "chat_memories": [
                {
                    "id": 1,
                    "created_at": "2024-01-01 10:00:00",
                    "summary": "Kept",
                    "category": "general",
                    "future_column": "dropped",
                },
            ],
            "table_from_the_future": [{"a": 1}],
        },
    }
    path = tmp_path / "drift.json"
    path.write_text(json.dumps(payload))

    summary = cli._import_data(path)

    assert summary["skipped_tables"] == ["table_from_the_future"]
    assert summary["skipped_columns"] == {"chat_memories": ["future_column"]}
    assert get_all_memories()[0]["summary"] == "Kept"


def test_failed_import_rolls_back_everything(tmp_db, tmp_path):
    save_memory("Pre-existing memory")
    payload = {
        "app": "lifter",
        "kind": "full",
        "tables": {
            "chat_memories": [
                {"created_at": "2024-01-01 10:00:00", "summary": "From backup", "category": "general"},
            ],
            # violates NOT NULL on description → whole transaction must roll back
            "user_goals": [{"type": "lift", "description": None}],
        },
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(Exception):
        cli._import_data(path)

    assert count_memories() == 1
    assert get_all_memories()[0]["summary"] == "Pre-existing memory"
