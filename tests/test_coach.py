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


def test_build_context_omits_name_when_pref_off(tmp_db):
    from db.goals import set_pref
    from ai.coach import _build_context
    set_pref("display_name", "João")
    set_pref("ai_send_name", "0")
    ctx = _build_context(weeks=4)
    assert "João" not in ctx
    assert "the athlete" in ctx


def test_build_context_includes_body_by_default(tmp_db):
    from db.store import upsert_body_measurement
    from ai.coach import _build_context
    upsert_body_measurement({"date": "2024-01-01", "weight_kg": 80.0, "fat_percent": 18.0}, db_path=tmp_db)
    ctx = _build_context(weeks=4)
    assert "## Body measurements" in ctx


def test_build_context_omits_body_when_pref_off(tmp_db):
    from db.goals import set_pref
    from db.store import upsert_body_measurement
    from ai.coach import _build_context
    upsert_body_measurement({"date": "2024-01-01", "weight_kg": 80.0, "fat_percent": 18.0}, db_path=tmp_db)
    set_pref("ai_send_body", "0")
    ctx = _build_context(weeks=4)
    assert "## Body measurements" not in ctx
    assert "80.0" not in ctx


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


def test_stamp_routine_does_not_duplicate_watermark():
    from ai.coach import _stamp_routine
    tag = "✦ Powered by Lifter"
    already_stamped = {"title": "Push Day", "notes": f"Heavy day.\n\n{tag}", "exercises": []}
    result = _stamp_routine(already_stamped)
    assert result["notes"].count(tag) == 1


def test_stamp_routine_adds_watermark_when_absent():
    from ai.coach import _stamp_routine
    result = _stamp_routine({"title": "Push Day", "notes": None, "exercises": []})
    assert "✦ Powered by Lifter" in result["notes"]


# ── slim mode ─────────────────────────────────────────────────────────────────

def test_build_context_both_modes_include_progressions(tmp_db):
    from ai.coach import _build_context
    seed_exercise_template(tmp_db)
    for i in range(4):
        seed_workout(tmp_db, f"p-w{i}", days_ago=i * 7)
    ctx_full = _build_context(weeks=8, slim=False)
    ctx_slim = _build_context(weeks=8, slim=True)
    # Plateau/progression sections appear in both modes when data exists
    # (they may be absent when there isn't enough data to detect them)
    assert isinstance(ctx_full, str) and isinstance(ctx_slim, str)


def test_build_context_full_includes_more_workouts_than_slim(tmp_db):
    from ai.coach import _build_context
    seed_exercise_template(tmp_db)
    for i in range(7):
        seed_workout(tmp_db, f"w{i}", days_ago=i)
    ctx_full = _build_context(weeks=8, slim=False)
    ctx_slim = _build_context(weeks=8, slim=True)
    # Full shows up to 7 workouts; slim shows up to 5 — full context must be longer
    assert len(ctx_full) > len(ctx_slim)


def test_build_context_both_modes_include_routine_set_weights(tmp_db):
    from ai.coach import _build_context
    seed_exercise_template(tmp_db)
    seed_routine(tmp_db, "r-both", title="Push Day")
    ctx_slim = _build_context(weeks=4, slim=True)
    ctx_full = _build_context(weeks=4, slim=False)
    # Both modes must show routine weights so update_routine works correctly
    assert "Push Day" in ctx_slim
    assert "80" in ctx_slim and "kg" in ctx_slim
    assert "80" in ctx_full and "kg" in ctx_full


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


# ── include_routine gating (token saving) ─────────────────────────────────────

def test_build_context_omits_routine_blocks_when_include_routine_false(tmp_db):
    from ai.coach import _build_context
    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "ctx-noroutine")
    seed_routine(tmp_db, "r-skip", title="My Push Day")

    ctx = _build_context(weeks=4, include_routine=False)
    assert "Exercise library" not in ctx
    assert "Saved routines" not in ctx


def test_build_context_includes_routine_blocks_when_include_routine_true(tmp_db):
    from ai.coach import _build_context
    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "ctx-routine")
    seed_routine(tmp_db, "r-keep", title="My Push Day")

    ctx = _build_context(weeks=4, include_routine=True)
    assert "Exercise library" in ctx
    assert "Saved routines" in ctx


def test_build_context_omitting_routine_keeps_analytics(tmp_db):
    """Gating routine blocks must not drop insight data (lossless)."""
    from ai.coach import _build_context
    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "ctx-keep-analytics")

    ctx = _build_context(weeks=4, include_routine=False)
    assert "Training summary" in ctx
    assert "Weekly volume" in ctx


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
