"""Tests for ai.coach — context building and routine push."""
from unittest.mock import MagicMock, patch
from tests.conftest import seed_exercise_template, seed_workout, seed_routine, TEMPLATE_ID


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


# ── saved-routines context ────────────────────────────────────────────────────

def test_build_context_includes_saved_routines(tmp_db):
    from ai.coach import _build_context
    seed_exercise_template(tmp_db)
    seed_routine(tmp_db, "r-ctx1", title="My Push Day")

    ctx = _build_context(weeks=4)
    assert "Saved routines" in ctx
    assert "My Push Day" in ctx


def test_build_context_no_routines_omits_section(tmp_db):
    from ai.coach import _build_context
    ctx = _build_context(weeks=4)
    assert "Saved routines" not in ctx


def test_build_context_routine_shows_exercise_name(tmp_db):
    from ai.coach import _build_context
    seed_exercise_template(tmp_db, template_id=TEMPLATE_ID)
    seed_routine(tmp_db, "r-ctx2", title="Pull Day", template_id=TEMPLATE_ID)

    ctx = _build_context(weeks=4)
    assert f"Exercise {TEMPLATE_ID}" in ctx


def test_build_context_multiple_routines(tmp_db):
    from ai.coach import _build_context
    seed_exercise_template(tmp_db)
    seed_routine(tmp_db, "r-a", title="Routine A")
    seed_routine(tmp_db, "r-b", title="Routine B")

    ctx = _build_context(weeks=4)
    assert "Routine A" in ctx
    assert "Routine B" in ctx


# ── update_routine tool ───────────────────────────────────────────────────────

def test_show_and_confirm_routine_update_calls_hevy_update(tmp_db):
    from ai.coach import _show_and_confirm_routine_update

    seed_exercise_template(tmp_db)
    seed_routine(tmp_db, "r-upd", title="Old Title")

    mock_client = MagicMock()
    mock_client.update_routine.return_value = {}

    with patch("hevy.client.HevyClient", return_value=mock_client), \
         patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.ask.return_value = True
        result = _show_and_confirm_routine_update({
            "routine_id": "r-upd",
            "title": "New Title",
            "notes": "Updated notes",
            "exercises": [],
        })

    assert result["success"] is True
    assert result["routine_id"] == "r-upd"
    mock_client.update_routine.assert_called_once()
    call_id = mock_client.update_routine.call_args[0][0]
    assert call_id == "r-upd"


def test_show_and_confirm_routine_update_upserts_to_local_db(tmp_db):
    from ai.coach import _show_and_confirm_routine_update
    from db.store import get_routines_with_exercises

    seed_exercise_template(tmp_db)
    seed_routine(tmp_db, "r-local", title="Before")

    mock_client = MagicMock()
    mock_client.update_routine.return_value = {}

    with patch("hevy.client.HevyClient", return_value=mock_client), \
         patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.ask.return_value = True
        _show_and_confirm_routine_update({
            "routine_id": "r-local",
            "title": "After",
            "notes": None,
            "exercises": [],
        })

    routines = get_routines_with_exercises(db_path=tmp_db)
    updated = next(r for r in routines if r["id"] == "r-local")
    assert updated["title"] == "After"


def test_show_and_confirm_routine_update_declined_returns_failure(tmp_db):
    from ai.coach import _show_and_confirm_routine_update

    seed_exercise_template(tmp_db)
    seed_routine(tmp_db, "r-decline", title="Existing")

    with patch("hevy.client.HevyClient"), \
         patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.ask.return_value = False
        result = _show_and_confirm_routine_update({
            "routine_id": "r-decline",
            "title": "New",
            "exercises": [],
        })

    assert result["success"] is False
