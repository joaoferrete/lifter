"""Tests for the SQLite persistence layer."""

from tests.conftest import seed_exercise_template, seed_routine, seed_workout


def test_init_db_creates_all_tables(tmp_db):
    from db.store import query

    tables = {r["name"] for r in query("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {
        "workouts",
        "workout_exercises",
        "workout_sets",
        "exercise_templates",
        "body_measurements",
        "fit_sleep",
        "fit_daily",
        "user_goals",
        "user_preferences",
        "chat_memories",
        "sync_state",
        "routines",
        "routine_exercises",
        "routine_sets",
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
    seed_workout(
        tmp_db,
        "w2",
        sets=[
            {"index": 0, "type": "normal", "weight_kg": 100.0, "reps": 3},
            {"index": 1, "type": "normal", "weight_kg": 90.0, "reps": 5},
        ],
    )

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
    from db.store import query, upsert_body_measurement

    upsert_body_measurement({"date": "2024-01-15", "weight_kg": 80.5, "fat_percent": 18.0}, db_path=tmp_db)
    rows = query("SELECT * FROM body_measurements WHERE date = ?", ("2024-01-15",))
    assert len(rows) == 1
    assert rows[0]["weight_kg"] == 80.5

    # Update
    upsert_body_measurement({"date": "2024-01-15", "weight_kg": 79.0, "fat_percent": 17.5}, db_path=tmp_db)
    rows = query("SELECT * FROM body_measurements WHERE date = ?", ("2024-01-15",))
    assert len(rows) == 1
    assert rows[0]["weight_kg"] == 79.0


# ── routines ──────────────────────────────────────────────────────────────────


def test_upsert_routine_stores_title(tmp_db):
    from db.store import query

    seed_exercise_template(tmp_db)
    seed_routine(tmp_db, "r1", title="Push Day")

    rows = query("SELECT * FROM routines WHERE id = ?", ("r1",))
    assert len(rows) == 1
    assert rows[0]["title"] == "Push Day"


def test_upsert_routine_stores_exercises_and_sets(tmp_db):
    from db.store import query

    seed_exercise_template(tmp_db)
    seed_routine(tmp_db, "r2")

    exercises = query("SELECT * FROM routine_exercises WHERE routine_id = ?", ("r2",))
    assert len(exercises) == 1

    sets = query(
        "SELECT rs.* FROM routine_sets rs "
        "JOIN routine_exercises re ON re.id = rs.routine_exercise_id "
        "WHERE re.routine_id = ?",
        ("r2",),
    )
    assert len(sets) == 2
    assert {s["weight_kg"] for s in sets} == {80.0}


def test_upsert_routine_replaces_exercises_on_update(tmp_db):
    from db.store import query, upsert_routine

    seed_exercise_template(tmp_db)
    seed_routine(tmp_db, "r3")

    # Update with a different exercise list (no sets)
    upsert_routine(
        {"id": "r3", "title": "Push Day v2", "notes": None, "exercises": []},
        db_path=tmp_db,
    )

    rows = query("SELECT * FROM routines WHERE id = ?", ("r3",))
    assert rows[0]["title"] == "Push Day v2"
    exercises = query("SELECT * FROM routine_exercises WHERE routine_id = ?", ("r3",))
    assert exercises == []


def test_delete_routine_removes_row(tmp_db):
    from db.store import delete_routine, query

    seed_exercise_template(tmp_db)
    seed_routine(tmp_db, "r4")

    delete_routine("r4", db_path=tmp_db)

    assert query("SELECT * FROM routines WHERE id = ?", ("r4",)) == []


def test_delete_routine_cascades_to_exercises_and_sets(tmp_db):
    from db.store import delete_routine, query

    seed_exercise_template(tmp_db)
    seed_routine(tmp_db, "r5")

    # Confirm exercises + sets exist before delete
    assert query("SELECT * FROM routine_exercises WHERE routine_id = ?", ("r5",)) != []

    delete_routine("r5", db_path=tmp_db)

    assert query("SELECT * FROM routine_exercises WHERE routine_id = ?", ("r5",)) == []


def test_delete_stale_routines_removes_unlisted(tmp_db):
    from db.store import delete_stale_routines, query

    seed_exercise_template(tmp_db)
    seed_routine(tmp_db, "keep1")
    seed_routine(tmp_db, "keep2")
    seed_routine(tmp_db, "stale1")

    deleted = delete_stale_routines({"keep1", "keep2"}, db_path=tmp_db)

    assert deleted == 1
    assert query("SELECT id FROM routines WHERE id = ?", ("stale1",)) == []
    assert query("SELECT id FROM routines WHERE id = ?", ("keep1",)) != []


def test_delete_stale_routines_keeps_all_when_all_present(tmp_db):
    from db.store import delete_stale_routines

    seed_exercise_template(tmp_db)
    seed_routine(tmp_db, "r1")
    seed_routine(tmp_db, "r2")

    deleted = delete_stale_routines({"r1", "r2"}, db_path=tmp_db)
    assert deleted == 0


def test_delete_stale_routines_empty_db_returns_zero(tmp_db):
    from db.store import delete_stale_routines

    deleted = delete_stale_routines({"nonexistent"}, db_path=tmp_db)
    assert deleted == 0


def test_get_routines_with_exercises_empty_db(tmp_db):
    from db.store import get_routines_with_exercises

    assert get_routines_with_exercises(db_path=tmp_db) == []


def test_get_routines_with_exercises_returns_nested_structure(tmp_db):
    from db.store import get_routines_with_exercises

    seed_exercise_template(tmp_db)
    seed_routine(tmp_db, "r-nested", title="Leg Day")

    routines = get_routines_with_exercises(db_path=tmp_db)
    assert len(routines) == 1
    r = routines[0]
    assert r["title"] == "Leg Day"
    assert len(r["exercises"]) == 1
    ex = r["exercises"][0]
    assert ex["title"] is not None
    assert len(ex["sets"]) == 2
    assert ex["sets"][0]["weight_kg"] == 80.0


def test_get_routines_with_exercises_uses_template_title_as_fallback(tmp_db):
    from db.store import get_routines_with_exercises, upsert_routine

    seed_exercise_template(tmp_db, template_id="T-FALLBACK", muscle="back")

    # Exercise stored with no title — should fall back to exercise_templates.title
    upsert_routine(
        {
            "id": "r-fb",
            "title": "Pull Day",
            "notes": None,
            "exercises": [
                {
                    "exercise_template_id": "T-FALLBACK",
                    "title": None,
                    "notes": None,
                    "rest_seconds": 60,
                    "sets": [],
                }
            ],
        },
        db_path=tmp_db,
    )

    routines = get_routines_with_exercises(db_path=tmp_db)
    ex = routines[0]["exercises"][0]
    assert ex["title"] == "Exercise T-FALLBACK"


# ── sync result records ───────────────────────────────────────────────────────


def test_record_and_get_sync_result(tmp_db):
    from db.store import get_sync_result, record_sync_result

    record_sync_result("last_sync_result", True, "full: 5 workouts", db_path=tmp_db)
    res = get_sync_result("last_sync_result", db_path=tmp_db)
    assert res is not None
    assert res["ok"] is True
    assert res["detail"] == "full: 5 workouts"
    assert "T" in res["when"]

    record_sync_result("last_sync_result", False, "HevyAPIError: 401", db_path=tmp_db)
    res_fail = get_sync_result("last_sync_result", db_path=tmp_db)
    assert res_fail is not None
    assert res_fail["ok"] is False


def test_get_sync_result_missing_or_corrupt(tmp_db):
    from db.store import get_sync_result, set_sync_state

    assert get_sync_result("last_sync_result", db_path=tmp_db) is None
    set_sync_state("last_sync_result", "not-json{", db_path=tmp_db)
    assert get_sync_result("last_sync_result", db_path=tmp_db) is None
    set_sync_state("last_sync_result", "[1,2]", db_path=tmp_db)
    assert get_sync_result("last_sync_result", db_path=tmp_db) is None


def test_sync_result_detail_truncated(tmp_db):
    from db.store import get_sync_result, record_sync_result

    record_sync_result("last_sync_result", False, "x" * 500, db_path=tmp_db)
    res = get_sync_result("last_sync_result", db_path=tmp_db)
    assert res is not None
    assert len(res["detail"]) == 200
