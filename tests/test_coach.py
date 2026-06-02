"""Tests for ai.coach — context building and routine push."""
from unittest.mock import MagicMock, patch
from tests.conftest import seed_exercise_template, seed_workout


# ── _build_context ────────────────────────────────────────────────────────────

def test_build_context_empty_db_returns_string(tmp_db):
    from ai.coach import _build_context
    ctx = _build_context(weeks=4)
    assert isinstance(ctx, str)
    assert len(ctx) > 0


def test_build_context_includes_athlete_section(tmp_db):
    from ai.coach import _build_context
    ctx = _build_context(weeks=4)
    assert "## Athlete" in ctx


def test_build_context_includes_athlete_name(tmp_db):
    from db.goals import set_pref
    from ai.coach import _build_context
    set_pref("display_name", "João")
    ctx = _build_context(weeks=4)
    assert "João" in ctx


def test_build_context_includes_workout_count(tmp_db):
    from ai.coach import _build_context
    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "ctx-w1", days_ago=3)
    seed_workout(tmp_db, "ctx-w2", days_ago=7)
    ctx = _build_context(weeks=4)
    assert "Total workouts" in ctx


def test_build_context_includes_goal_section_when_goals_set(tmp_db):
    from db.goals import save_goal
    from ai.coach import _build_context
    save_goal(type="frequency", description="Train 4×/week", target=4.0, unit="sessions/wk")
    ctx = _build_context(weeks=4)
    assert "Train 4×/week" in ctx


def test_build_context_includes_exercise_library_when_templates_exist(tmp_db):
    from ai.coach import _build_context
    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "ctx-w3")
    ctx = _build_context(weeks=4)
    assert "Exercise library" in ctx


# ── push_routine_to_hevy ──────────────────────────────────────────────────────

def test_push_routine_calls_create_routine():
    from ai.coach import push_routine_to_hevy

    mock_instance = MagicMock()
    mock_instance.create_routine.return_value = {"routine": {"id": "abc123"}}

    with patch("hevy.client.HevyClient", return_value=mock_instance):
        push_routine_to_hevy({"title": "Push Day", "exercises": []})

    mock_instance.create_routine.assert_called_once()


def test_push_routine_stamps_watermark():
    from ai.coach import push_routine_to_hevy

    captured = {}
    mock_instance = MagicMock()

    def capture_create(routine):
        captured["routine"] = routine
        return {"routine": {"id": "x"}}

    mock_instance.create_routine.side_effect = capture_create

    with patch("hevy.client.HevyClient", return_value=mock_instance):
        push_routine_to_hevy({"title": "Push Day", "exercises": []})

    assert "✦ Powered by Lifter" in captured["routine"].get("notes", "")


def test_push_routine_preserves_existing_notes():
    from ai.coach import push_routine_to_hevy

    captured = {}
    mock_instance = MagicMock()

    def capture_create(routine):
        captured["routine"] = routine
        return {"routine": {"id": "x"}}

    mock_instance.create_routine.side_effect = capture_create

    with patch("hevy.client.HevyClient", return_value=mock_instance):
        push_routine_to_hevy({"title": "Pull Day", "notes": "Focus on back.", "exercises": []})

    notes = captured["routine"]["notes"]
    assert "Focus on back." in notes
    assert "✦ Powered by Lifter" in notes


# ── _routine_id (hevy/client.py) ──────────────────────────────────────────────

def test_routine_id_from_wrapped_response():
    from hevy.client import _routine_id
    assert _routine_id({"routine": {"id": "abc"}}) == "abc"


def test_routine_id_from_flat_response():
    from hevy.client import _routine_id
    assert _routine_id({"id": "flat-id"}) == "flat-id"


def test_routine_id_from_list_response():
    from hevy.client import _routine_id
    assert _routine_id([{"id": "list-id"}]) == "list-id"


def test_routine_id_empty_list():
    from hevy.client import _routine_id
    assert _routine_id([]) == ""


def test_routine_id_non_dict():
    from hevy.client import _routine_id
    assert _routine_id("unexpected") == ""
