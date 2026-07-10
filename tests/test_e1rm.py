"""The canonical e1RM must agree between the Python helper and the SQL fragment,
including the reps == 1 special case that the old inline SQL got wrong."""

import pytest

from analytics.e1rm import e1rm
from tests.conftest import seed_exercise_template, seed_workout


def test_single_rep_is_raw_weight():
    assert e1rm(100.0, 1) == 100.0


def test_epley_for_multiple_reps():
    assert e1rm(100.0, 5) == pytest.approx(100 * (1 + 5 / 30))


def test_sql_and_python_agree_for_singles(tmp_db):
    from db.store import query

    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "w1", sets=[{"index": 0, "type": "normal", "weight_kg": 100.0, "reps": 1}])

    from analytics.e1rm import NORMAL_SET_FILTER_SQL, e1rm_sql

    rows = query(f"SELECT MAX({e1rm_sql()}) AS m FROM workout_sets ws WHERE {NORMAL_SET_FILTER_SQL}")
    assert rows[0]["m"] == pytest.approx(e1rm(100.0, 1)) == 100.0


def test_sql_and_python_agree_for_fives(tmp_db):
    from db.store import query

    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "w1", sets=[{"index": 0, "type": "normal", "weight_kg": 90.0, "reps": 5}])

    from analytics.e1rm import NORMAL_SET_FILTER_SQL, e1rm_sql

    rows = query(f"SELECT MAX({e1rm_sql()}) AS m FROM workout_sets ws WHERE {NORMAL_SET_FILTER_SQL}")
    assert rows[0]["m"] == pytest.approx(e1rm(90.0, 5))


def test_lift_pr_goal_uses_raw_weight_for_singles(tmp_db):
    """A 100 kg single against a 100 kg goal is achieved — not 103.3 kg."""
    from analytics.goal_progress import compute_goal_progress
    from db.goals import save_goal
    from tests.conftest import TEMPLATE_ID

    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "w1", sets=[{"index": 0, "type": "normal", "weight_kg": 100.0, "reps": 1}])
    save_goal(
        type="lift_pr",
        description="Bench 100",
        target=100.0,
        unit="kg",
        exercise_template_id=TEMPLATE_ID,
        exercise_name="Bench",
    )
    p = compute_goal_progress()[0]
    assert p["current"] == 100.0
    assert p["achieved"] is True
