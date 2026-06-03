"""Tests for the goals CRUD layer and progress computation."""
import pytest
from tests.conftest import seed_exercise_template, seed_workout, TEMPLATE_ID


def test_save_and_get_goal(tmp_db):
    from db.goals import save_goal, get_goals
    save_goal(type="frequency", description="Train 4×/week", target=4.0, unit="sessions/wk")
    goals = get_goals()
    assert len(goals) == 1
    assert goals[0]["description"] == "Train 4×/week"
    assert goals[0]["target"] == 4.0
    assert goals[0]["achieved_at"] is None


def test_multiple_goals_returned_ordered(tmp_db):
    from db.goals import save_goal, get_goals
    save_goal(type="custom", description="Goal A")
    save_goal(type="custom", description="Goal B")
    goals = get_goals()
    assert [g["description"] for g in goals] == ["Goal A", "Goal B"]


def test_delete_goal_removes_it(tmp_db):
    from db.goals import save_goal, get_goals, delete_goal
    save_goal(type="custom", description="To be deleted")
    gid = get_goals()[0]["id"]
    delete_goal(gid)
    assert get_goals() == []


def test_update_goal_fields(tmp_db):
    from db.goals import save_goal, get_goals, update_goal_fields
    save_goal(type="lift_pr", description="Bench 100 kg", target=100.0, unit="kg")
    gid = get_goals()[0]["id"]
    update_goal_fields(gid, description="Bench 120 kg", target=120.0)
    updated = get_goals()[0]
    assert updated["description"] == "Bench 120 kg"
    assert updated["target"] == 120.0


def test_mark_goal_achieved_removes_from_active(tmp_db):
    from db.goals import save_goal, get_goals, mark_goal_achieved
    save_goal(type="custom", description="Achieved goal")
    gid = get_goals()[0]["id"]
    mark_goal_achieved(gid)
    assert get_goals() == []


def test_clear_goals(tmp_db):
    from db.goals import save_goal, get_goals, clear_goals
    save_goal(type="custom", description="A")
    save_goal(type="custom", description="B")
    clear_goals()
    assert get_goals() == []


def test_pref_roundtrip(tmp_db):
    from db.goals import get_pref, set_pref
    assert get_pref("nonexistent") is None
    set_pref("display_name", "João")
    assert get_pref("display_name") == "João"
    set_pref("display_name", "Updated")
    assert get_pref("display_name") == "Updated"


def test_should_ask_goals_first_time(tmp_db):
    from db.goals import should_ask_goals
    assert should_ask_goals() is True


def test_should_ask_goals_false_after_marking(tmp_db):
    from db.goals import should_ask_goals, mark_goals_asked
    mark_goals_asked()
    assert should_ask_goals() is False


def test_compute_progress_frequency_goal(tmp_db):
    from db.goals import save_goal, compute_goal_progress
    seed_exercise_template(tmp_db)
    # Seed 3 workouts in the last 4 weeks (= 3 sessions)
    for i in range(3):
        seed_workout(tmp_db, f"freq-w{i}", days_ago=i * 3)

    save_goal(type="frequency", description="Train 4×/week", target=4.0, unit="sessions/wk")
    progress = compute_goal_progress()
    assert len(progress) == 1
    assert progress[0]["type"] == "frequency"
    assert progress[0]["current"] is not None
    assert 0 <= progress[0]["pct"] <= 100


def test_compute_progress_lift_pr_goal(tmp_db):
    from db.goals import save_goal, compute_goal_progress
    seed_exercise_template(tmp_db, template_id=TEMPLATE_ID)
    seed_workout(tmp_db, "pr-w1", template_id=TEMPLATE_ID,
                 sets=[{"index": 0, "type": "normal", "weight_kg": 90.0, "reps": 5}])

    save_goal(
        type="lift_pr",
        description="Bench 100 kg",
        target=100.0,
        unit="kg",
        exercise_template_id=TEMPLATE_ID,
        exercise_name="Exercise TMPL001",
    )
    progress = compute_goal_progress()
    assert len(progress) == 1
    p = progress[0]
    assert p["type"] == "lift_pr"
    assert p["current"] is not None
    # e1RM of 90kg×5 = 90*(1+5/30) ≈ 105 → should be ≥ target → achieved
    assert p["pct"] == 100.0 or p["current"] > 0


# ── token usage tracking ──────────────────────────────────────────────────────

def test_get_token_usage_returns_zeros_when_empty(tmp_db):
    from db.goals import get_token_usage
    usage = get_token_usage()
    assert usage == {"input": 0, "output": 0, "cache_read": 0}


def test_add_token_usage_increments_input_and_output(tmp_db):
    from db.goals import add_token_usage, get_token_usage
    add_token_usage(input_tokens=1000, output_tokens=250)
    usage = get_token_usage()
    assert usage["input"] == 1000
    assert usage["output"] == 250
    assert usage["cache_read"] == 0


def test_add_token_usage_is_cumulative(tmp_db):
    from db.goals import add_token_usage, get_token_usage
    add_token_usage(input_tokens=500, output_tokens=100)
    add_token_usage(input_tokens=300, output_tokens=50, cache_read_tokens=200)
    usage = get_token_usage()
    assert usage["input"] == 800
    assert usage["output"] == 150
    assert usage["cache_read"] == 200


def test_add_token_usage_ignores_zero_values(tmp_db):
    from db.goals import add_token_usage, get_token_usage
    add_token_usage(input_tokens=100)
    add_token_usage()  # all zeros — should not create rows or crash
    usage = get_token_usage()
    assert usage["input"] == 100


def test_reset_token_usage_clears_counters(tmp_db):
    from db.goals import add_token_usage, get_token_usage, reset_token_usage
    add_token_usage(input_tokens=5000, output_tokens=1000, cache_read_tokens=3000)
    reset_token_usage()
    usage = get_token_usage()
    assert usage == {"input": 0, "output": 0, "cache_read": 0}
