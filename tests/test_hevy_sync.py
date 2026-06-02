"""Tests for hevy.sync — full and incremental sync logic with mocked client and store."""
from datetime import datetime, timezone
from unittest.mock import MagicMock


def _fake_workout(wid):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": wid,
        "title": f"Workout {wid}",
        "description": None,
        "routine_id": None,
        "start_time": ts,
        "end_time": ts,
        "updated_at": ts,
        "created_at": ts,
        "exercises": [],
    }


def _fake_template(tid):
    return {
        "id": tid,
        "title": f"Exercise {tid}",
        "type": "weight_reps",
        "primary_muscle_group": "chest",
        "secondary_muscle_groups": [],
        "is_custom": False,
    }


def _patch_sync(monkeypatch, sync_mod, calls=None):
    if calls is None:
        calls = {"workouts": [], "templates": [], "body_measurements": [], "deleted": [], "sync_states": {}}
    monkeypatch.setattr(sync_mod, "init_db", lambda: None)
    monkeypatch.setattr(sync_mod, "upsert_workout", lambda w: calls["workouts"].append(w["id"]))
    monkeypatch.setattr(sync_mod, "delete_workout", lambda wid: calls["deleted"].append(wid))
    monkeypatch.setattr(sync_mod, "upsert_exercise_template", lambda t: calls["templates"].append(t["id"]))
    monkeypatch.setattr(sync_mod, "upsert_body_measurement", lambda m: calls["body_measurements"].append(m))
    monkeypatch.setattr(sync_mod, "set_sync_state", lambda k, v: calls["sync_states"].update({k: v}))
    return calls


# ── full_sync ─────────────────────────────────────────────────────────────────

def test_full_sync_returns_correct_workout_count(monkeypatch):
    import hevy.sync as sync_mod
    calls = _patch_sync(monkeypatch, sync_mod)

    mock_client = MagicMock()
    mock_client.get_workout_count.return_value = 2
    mock_client.get_workouts.return_value = iter([_fake_workout("w1"), _fake_workout("w2")])
    mock_client.get_exercise_templates.return_value = iter([])
    mock_client.get_body_measurements.return_value = iter([])

    counts = sync_mod.full_sync(mock_client)

    assert counts["workouts"] == 2
    assert calls["workouts"] == ["w1", "w2"]


def test_full_sync_returns_template_and_measurement_counts(monkeypatch):
    import hevy.sync as sync_mod
    _patch_sync(monkeypatch, sync_mod)

    mock_client = MagicMock()
    mock_client.get_workout_count.return_value = 0
    mock_client.get_workouts.return_value = iter([])
    mock_client.get_exercise_templates.return_value = iter([_fake_template("T1"), _fake_template("T2")])
    mock_client.get_body_measurements.return_value = iter([{"date": "2024-01-01", "weight_kg": 80.0}])

    counts = sync_mod.full_sync(mock_client)

    assert counts["templates"] == 2
    assert counts["body_measurements"] == 1


def test_full_sync_sets_last_sync_state(monkeypatch):
    import hevy.sync as sync_mod
    calls = _patch_sync(monkeypatch, sync_mod)

    mock_client = MagicMock()
    mock_client.get_workout_count.return_value = 0
    mock_client.get_workouts.return_value = iter([])
    mock_client.get_exercise_templates.return_value = iter([])
    mock_client.get_body_measurements.return_value = iter([])

    sync_mod.full_sync(mock_client)

    assert "last_sync" in calls["sync_states"]


# ── incremental_sync ──────────────────────────────────────────────────────────

def test_incremental_sync_falls_back_to_full_when_no_last_sync(monkeypatch):
    import hevy.sync as sync_mod
    calls = _patch_sync(monkeypatch, sync_mod)
    monkeypatch.setattr(sync_mod, "get_sync_state", lambda k: None)

    full_sync_calls = []
    original_full = sync_mod.full_sync

    def mock_full(client):
        full_sync_calls.append(True)
        return {"workouts": 0, "templates": 0, "body_measurements": 0, "updated_ids": [], "since": None}

    monkeypatch.setattr(sync_mod, "full_sync", mock_full)
    sync_mod.incremental_sync(MagicMock())
    assert full_sync_calls == [True]


def test_incremental_sync_handles_updated_events(monkeypatch):
    import hevy.sync as sync_mod
    calls = _patch_sync(monkeypatch, sync_mod)
    monkeypatch.setattr(sync_mod, "get_sync_state", lambda k: "2024-01-01T00:00:00Z")

    mock_client = MagicMock()
    mock_client.get_workout_events.return_value = iter([
        {"type": "updated", "workout": _fake_workout("w1")},
        {"type": "updated", "workout": _fake_workout("w2")},
    ])
    mock_client.get_exercise_templates.return_value = iter([])
    mock_client.get_body_measurements.return_value = iter([])

    counts = sync_mod.incremental_sync(mock_client)

    assert counts["updated"] == 2
    assert set(counts["updated_ids"]) == {"w1", "w2"}
    assert calls["workouts"] == ["w1", "w2"]


def test_incremental_sync_handles_deleted_events(monkeypatch):
    import hevy.sync as sync_mod
    calls = _patch_sync(monkeypatch, sync_mod)
    monkeypatch.setattr(sync_mod, "get_sync_state", lambda k: "2024-01-01T00:00:00Z")

    mock_client = MagicMock()
    mock_client.get_workout_events.return_value = iter([
        {"type": "deleted", "id": "gone1"},
        {"type": "deleted", "id": "gone2"},
    ])
    mock_client.get_exercise_templates.return_value = iter([])
    mock_client.get_body_measurements.return_value = iter([])

    counts = sync_mod.incremental_sync(mock_client)

    assert counts["deleted"] == 2
    assert calls["deleted"] == ["gone1", "gone2"]
