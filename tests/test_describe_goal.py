"""describe_goal — localized display text rebuilt from structured goal fields."""

import i18n
from commands.goals import describe_goal


def test_frequency_goal_localized(tmp_db):
    goal = {"type": "frequency", "target": 4.0, "description": "Train 4× per week"}
    i18n.init("pt_BR")
    try:
        assert describe_goal(goal) == "Treinar 4× por semana"
    finally:
        i18n.init("en")
    assert describe_goal(goal) == "Train 4× per week"


def test_lift_pr_uses_exercise_and_formatted_weight(tmp_db):
    goal = {"type": "lift_pr", "target": 100.0, "exercise_name": "Bench Press", "description": "old text"}
    text = describe_goal(goal)
    assert "Bench Press" in text
    assert "100 kg" in text


def test_volume_goal_titles_muscle(tmp_db):
    goal = {"type": "volume", "target": 15.0, "muscle_group": "upper_back", "description": "x"}
    assert "Upper Back" in describe_goal(goal)
    assert "15" in describe_goal(goal)


def test_custom_goal_falls_back_to_stored_description(tmp_db):
    goal = {"type": "custom", "target": None, "description": "Sleep 8h every night"}
    assert describe_goal(goal) == "Sleep 8h every night"


def test_legacy_row_without_structured_fields_falls_back(tmp_db):
    goal = {"type": "lift_pr", "target": None, "description": "Bench — 100 kg"}
    assert describe_goal(goal) == "Bench — 100 kg"


def test_body_fat_goal(tmp_db):
    goal = {"type": "body_fat", "target": 15.0, "description": "x"}
    assert "15%" in describe_goal(goal)
