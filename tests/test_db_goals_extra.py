"""Extra goal-progress tests covering weight_loss, weight_gain, body_fat, volume, custom."""

from tests.conftest import seed_exercise_template, seed_workout

# ── weight_loss ───────────────────────────────────────────────────────────────


def test_weight_loss_zero_progress_at_start(tmp_db):
    from db.goals import compute_goal_progress, save_goal
    from db.store import upsert_body_measurement

    upsert_body_measurement({"date": "2024-01-01", "weight_kg": 85.0, "fat_percent": None}, db_path=tmp_db)
    save_goal(type="weight_loss", description="Lose 5kg", target=80.0, unit="kg", start_value=85.0)
    p = compute_goal_progress()[0]
    assert p["type"] == "weight_loss"
    assert p["current"] == 85.0
    assert p["pct"] == 0.0
    assert p["achieved"] is False


def test_weight_loss_partial_progress(tmp_db):
    from db.goals import compute_goal_progress, save_goal
    from db.store import upsert_body_measurement

    # start=90, target=80, current=85 → 50 %
    upsert_body_measurement({"date": "2024-01-01", "weight_kg": 85.0, "fat_percent": None}, db_path=tmp_db)
    save_goal(type="weight_loss", description="Lose 10kg", target=80.0, unit="kg", start_value=90.0)
    assert compute_goal_progress()[0]["pct"] == 50.0


def test_weight_loss_achieved_removes_from_active(tmp_db):
    from db.goals import compute_goal_progress, get_goals, save_goal
    from db.store import upsert_body_measurement

    upsert_body_measurement({"date": "2024-01-01", "weight_kg": 79.0, "fat_percent": None}, db_path=tmp_db)
    save_goal(type="weight_loss", description="Reach 80kg", target=80.0, unit="kg", start_value=90.0)
    progress = compute_goal_progress()
    assert progress[0]["achieved"] is True
    assert get_goals() == []


# ── weight_gain ───────────────────────────────────────────────────────────────


def test_weight_gain_partial_progress(tmp_db):
    from db.goals import compute_goal_progress, save_goal
    from db.store import upsert_body_measurement

    # start=70, target=80, current=75 → 50 %
    upsert_body_measurement({"date": "2024-01-01", "weight_kg": 75.0, "fat_percent": None}, db_path=tmp_db)
    save_goal(type="weight_gain", description="Gain 10kg", target=80.0, unit="kg", start_value=70.0)
    assert compute_goal_progress()[0]["pct"] == 50.0


def test_weight_gain_achieved(tmp_db):
    from db.goals import compute_goal_progress, get_goals, save_goal
    from db.store import upsert_body_measurement

    upsert_body_measurement({"date": "2024-01-01", "weight_kg": 82.0, "fat_percent": None}, db_path=tmp_db)
    save_goal(type="weight_gain", description="Reach 80kg", target=80.0, unit="kg", start_value=70.0)
    assert compute_goal_progress()[0]["achieved"] is True
    assert get_goals() == []


# ── body_fat ──────────────────────────────────────────────────────────────────


def test_body_fat_partial_progress(tmp_db):
    from db.goals import compute_goal_progress, save_goal
    from db.store import upsert_body_measurement

    # start=22, target=15, current=18.5 → 50 %
    upsert_body_measurement({"date": "2024-01-01", "weight_kg": 80.0, "fat_percent": 18.5}, db_path=tmp_db)
    save_goal(type="body_fat", description="12% body fat", target=15.0, unit="%", start_value=22.0)
    p = compute_goal_progress()[0]
    assert p["type"] == "body_fat"
    assert p["current"] == 18.5
    assert abs(p["pct"] - 50.0) < 0.1


def test_body_fat_achieved(tmp_db):
    from db.goals import compute_goal_progress, get_goals, save_goal
    from db.store import upsert_body_measurement

    upsert_body_measurement({"date": "2024-01-01", "weight_kg": 80.0, "fat_percent": 14.0}, db_path=tmp_db)
    save_goal(type="body_fat", description="15% body fat", target=15.0, unit="%", start_value=22.0)
    assert compute_goal_progress()[0]["achieved"] is True
    assert get_goals() == []


# ── volume ────────────────────────────────────────────────────────────────────


def test_volume_goal_tracks_sets_per_week(tmp_db):
    from db.goals import compute_goal_progress, save_goal

    seed_exercise_template(tmp_db, muscle="chest")
    for i in range(2):
        seed_workout(
            tmp_db,
            f"vol{i}",
            days_ago=i * 3,
            sets=[
                {"index": 0, "type": "normal", "weight_kg": 80.0, "reps": 5},
                {"index": 1, "type": "normal", "weight_kg": 80.0, "reps": 5},
            ],
        )
    save_goal(type="volume", description="15 chest sets/wk", target=15.0, unit="sets/wk", muscle_group="chest")
    p = compute_goal_progress()[0]
    assert p["type"] == "volume"
    assert p["current"] is not None
    assert 0 <= p["pct"] <= 100


# ── custom ────────────────────────────────────────────────────────────────────


def test_custom_goal_has_no_numeric_progress(tmp_db):
    from db.goals import compute_goal_progress, save_goal

    save_goal(type="custom", description="Sleep 8h every night")
    p = compute_goal_progress()[0]
    assert p["pct"] is None
    assert p["current"] is None


# ── get_all_goals ─────────────────────────────────────────────────────────────


def test_get_all_goals_includes_achieved(tmp_db):
    from db.goals import get_all_goals, get_goals, mark_goal_achieved, save_goal

    save_goal(type="custom", description="Active goal")
    save_goal(type="custom", description="Achieved goal")
    gid = get_goals()[1]["id"]
    mark_goal_achieved(gid)
    assert len(get_goals()) == 1
    assert len(get_all_goals()) == 2


def test_get_all_goals_ordered_by_id_desc(tmp_db):
    from db.goals import get_all_goals, save_goal

    save_goal(type="custom", description="First")
    save_goal(type="custom", description="Second")
    goals = get_all_goals()
    assert goals[0]["description"] == "Second"
    assert goals[1]["description"] == "First"


# ── negative progress, initial value, baseline backfill ───────────────────────


def test_weight_loss_negative_progress_when_worse(tmp_db):
    """Gaining weight on a weight-loss goal yields negative progress (not floored at 0)."""
    from db.goals import compute_goal_progress, save_goal
    from db.store import upsert_body_measurement

    # start=80, target=70, current=82 → (80-82)/(80-70) = -20 %
    upsert_body_measurement({"date": "2024-01-01", "weight_kg": 82.0}, db_path=tmp_db)
    save_goal(type="weight_loss", description="Lose to 70", target=70.0, unit="kg", start_value=80.0)
    p = compute_goal_progress()[0]
    assert p["pct"] == -20.0
    assert p["start"] == 80.0
    assert p["current"] == 82.0
    assert p["achieved"] is False


def test_body_fat_negative_progress_when_worse(tmp_db):
    from db.goals import compute_goal_progress, save_goal
    from db.store import upsert_body_measurement

    # start=20, target=15, current=22 → (20-22)/(20-15) = -40 %
    upsert_body_measurement({"date": "2024-01-01", "fat_percent": 22.0}, db_path=tmp_db)
    save_goal(type="body_fat", description="BF to 15", target=15.0, unit="%", start_value=20.0)
    p = compute_goal_progress()[0]
    assert p["pct"] == -40.0
    assert p["start"] == 20.0


def test_missing_start_value_is_backfilled(tmp_db):
    """A goal created without start_value (e.g. via the AI tool) gets its baseline seeded
    from the current measurement so progress isn't stuck at 0 with start==current forever."""
    from db.goals import compute_goal_progress, get_goals, save_goal
    from db.store import upsert_body_measurement

    upsert_body_measurement({"date": "2024-01-01", "weight_kg": 75.0}, db_path=tmp_db)
    save_goal(type="weight_loss", description="AI loss", target=70.0, unit="kg", start_value=None)
    p = compute_goal_progress()[0]
    assert p["start"] == 75.0
    assert p["pct"] == 0.0
    # baseline persisted to the row
    assert get_goals()[0]["start_value"] == 75.0


# ── token budget ──────────────────────────────────────────────────────────────


def test_token_budget_default_zero(tmp_db):
    from db.goals import get_token_budget, token_budget_status

    assert get_token_budget() == 0
    assert token_budget_status() is None


def test_token_budget_invalid_string_is_zero(tmp_db):
    from db.goals import get_token_budget, set_pref

    set_pref("ai_tokens_month_budget", "not-a-number")
    assert get_token_budget() == 0


def test_token_budget_status_pct_excludes_cache_read(tmp_db):
    from db.goals import add_token_usage, set_token_budget, token_budget_status

    set_token_budget(1000)
    add_token_usage(600, 300, cache_read_tokens=500)
    st = token_budget_status()
    assert st["used"] == 900
    assert st["budget"] == 1000
    assert st["pct"] == 90.0


# ── goal celebration watermark ────────────────────────────────────────────────


def test_uncelebrated_achievements_flow(tmp_db):
    from db.goals import (
        _conn,
        get_all_goals,
        get_pref,
        get_uncelebrated_achievements,
        mark_achievements_celebrated,
        mark_goal_achieved,
        save_goal,
    )

    save_goal(type="lift", description="Bench 100kg", target=100, unit="kg")
    save_goal(type="lift", description="Squat 140kg", target=140, unit="kg")
    ids = [g["id"] for g in get_all_goals()]
    for gid in ids:
        mark_goal_achieved(gid)

    achieved = get_uncelebrated_achievements()
    assert {g["description"] for g in achieved} == {"Bench 100kg", "Squat 140kg"}

    mark_achievements_celebrated()
    assert get_uncelebrated_achievements() == []

    # watermark copied verbatim from the DB's own timestamp format
    with _conn() as conn:
        max_at = conn.execute("SELECT MAX(achieved_at) AS m FROM user_goals").fetchone()["m"]
    assert get_pref("goals_celebrated_at") == max_at

    # a goal achieved later shows up alone
    save_goal(type="lift", description="Deadlift 180kg", target=180, unit="kg")
    gid3 = get_all_goals()[0]["id"]
    with _conn() as conn:
        conn.execute("UPDATE user_goals SET achieved_at = datetime('now', '+1 hour') WHERE id = ?", (gid3,))
    assert [g["description"] for g in get_uncelebrated_achievements()] == ["Deadlift 180kg"]
