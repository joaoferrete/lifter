"""Tests for hevy.sync — full and incremental sync logic with mocked client and store."""

from datetime import UTC, datetime
from unittest.mock import MagicMock


def _fake_workout(wid):
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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


def _fake_routine(rid):
    return {
        "id": rid,
        "title": f"Routine {rid}",
        "notes": None,
        "folder_id": None,
        "updated_at": "2024-01-01T00:00:00Z",
        "created_at": "2024-01-01T00:00:00Z",
        "exercises": [],
    }


def _patch_sync(monkeypatch, sync_mod, calls=None):
    if calls is None:
        calls = {
            "workouts": [],
            "templates": [],
            "body_measurements": [],
            "deleted": [],
            "routines": [],
            "sync_states": {},
            "sync_results": [],
        }
    calls.setdefault("sync_results", [])
    monkeypatch.setattr(sync_mod, "init_db", lambda: None)
    monkeypatch.setattr(sync_mod, "upsert_workout", lambda w: calls["workouts"].append(w["id"]))
    monkeypatch.setattr(sync_mod, "delete_workout", lambda wid: calls["deleted"].append(wid))
    monkeypatch.setattr(sync_mod, "upsert_exercise_template", lambda t: calls["templates"].append(t["id"]))
    monkeypatch.setattr(sync_mod, "upsert_body_measurement", lambda m: calls["body_measurements"].append(m))
    monkeypatch.setattr(sync_mod, "upsert_routine", lambda r: calls["routines"].append(r["id"]))
    monkeypatch.setattr(sync_mod, "delete_stale_routines", lambda ids: None)
    monkeypatch.setattr(sync_mod, "set_sync_state", lambda k, v: calls["sync_states"].update({k: v}))
    monkeypatch.setattr(
        sync_mod,
        "record_sync_result",
        lambda key, ok, detail="": calls["sync_results"].append({"key": key, "ok": ok, "detail": detail}),
    )
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
    mock_client.get_routines.return_value = iter([])

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
    mock_client.get_routines.return_value = iter([])

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
    mock_client.get_routines.return_value = iter([])

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
    mock_client.get_workout_events.return_value = iter(
        [
            {"type": "updated", "workout": _fake_workout("w1")},
            {"type": "updated", "workout": _fake_workout("w2")},
        ]
    )
    mock_client.get_exercise_templates.return_value = iter([])
    mock_client.get_body_measurements.return_value = iter([])
    mock_client.get_routines.return_value = iter([])

    counts = sync_mod.incremental_sync(mock_client)

    assert counts["updated"] == 2
    assert set(counts["updated_ids"]) == {"w1", "w2"}
    assert calls["workouts"] == ["w1", "w2"]


def test_incremental_sync_handles_deleted_events(monkeypatch):
    import hevy.sync as sync_mod

    calls = _patch_sync(monkeypatch, sync_mod)
    monkeypatch.setattr(sync_mod, "get_sync_state", lambda k: "2024-01-01T00:00:00Z")

    mock_client = MagicMock()
    mock_client.get_workout_events.return_value = iter(
        [
            {"type": "deleted", "id": "gone1"},
            {"type": "deleted", "id": "gone2"},
        ]
    )
    mock_client.get_exercise_templates.return_value = iter([])
    mock_client.get_body_measurements.return_value = iter([])
    mock_client.get_routines.return_value = iter([])

    counts = sync_mod.incremental_sync(mock_client)

    assert counts["deleted"] == 2
    assert calls["deleted"] == ["gone1", "gone2"]


# ── routine syncing ───────────────────────────────────────────────────────────


def test_full_sync_syncs_routines(monkeypatch):
    import hevy.sync as sync_mod

    calls = _patch_sync(monkeypatch, sync_mod)

    mock_client = MagicMock()
    mock_client.get_workout_count.return_value = 0
    mock_client.get_workouts.return_value = iter([])
    mock_client.get_exercise_templates.return_value = iter([])
    mock_client.get_body_measurements.return_value = iter([])
    mock_client.get_routines.return_value = iter([_fake_routine("r1"), _fake_routine("r2")])

    counts = sync_mod.full_sync(mock_client)

    assert counts["routines"] == 2
    assert calls["routines"] == ["r1", "r2"]


def test_incremental_sync_syncs_routines(monkeypatch):
    import hevy.sync as sync_mod

    calls = _patch_sync(monkeypatch, sync_mod)
    monkeypatch.setattr(sync_mod, "get_sync_state", lambda k: "2024-01-01T00:00:00Z")

    mock_client = MagicMock()
    mock_client.get_workout_events.return_value = iter([])
    mock_client.get_exercise_templates.return_value = iter([])
    mock_client.get_body_measurements.return_value = iter([])
    mock_client.get_routines.return_value = iter([_fake_routine("r3")])

    counts = sync_mod.incremental_sync(mock_client)

    assert counts["routines"] == 1
    assert calls["routines"] == ["r3"]


def test_full_sync_calls_delete_stale_routines(monkeypatch):
    import hevy.sync as sync_mod

    _patch_sync(monkeypatch, sync_mod)

    stale_calls = []
    monkeypatch.setattr(sync_mod, "delete_stale_routines", lambda ids: stale_calls.append(ids))

    mock_client = MagicMock()
    mock_client.get_workout_count.return_value = 0
    mock_client.get_workouts.return_value = iter([])
    mock_client.get_exercise_templates.return_value = iter([])
    mock_client.get_body_measurements.return_value = iter([])
    mock_client.get_routines.return_value = iter([_fake_routine("r1")])

    sync_mod.full_sync(mock_client)

    assert stale_calls == [{"r1"}]


def test_full_sync_passes_empty_set_to_delete_stale_when_no_routines(monkeypatch):
    import hevy.sync as sync_mod

    _patch_sync(monkeypatch, sync_mod)

    stale_calls = []
    monkeypatch.setattr(sync_mod, "delete_stale_routines", lambda ids: stale_calls.append(ids))

    mock_client = MagicMock()
    mock_client.get_workout_count.return_value = 0
    mock_client.get_workouts.return_value = iter([])
    mock_client.get_exercise_templates.return_value = iter([])
    mock_client.get_body_measurements.return_value = iter([])
    mock_client.get_routines.return_value = iter([])

    sync_mod.full_sync(mock_client)

    assert stale_calls == [set()]


# ── sync result recording ─────────────────────────────────────────────────────


def test_full_sync_records_success(monkeypatch):
    import hevy.sync as sync_mod

    calls = _patch_sync(monkeypatch, sync_mod)
    client = MagicMock()
    client.get_workout_count.return_value = 2
    client.get_workouts.return_value = iter([_fake_workout("w1"), _fake_workout("w2")])
    client.get_exercise_templates.return_value = iter([])
    client.get_body_measurements.return_value = iter([])
    client.get_routines.return_value = iter([])

    sync_mod.full_sync(client)

    assert calls["sync_results"] == [{"key": "last_sync_result", "ok": True, "detail": "full: 2 workouts"}]


def test_full_sync_records_failure_and_reraises(monkeypatch):
    import pytest

    import hevy.sync as sync_mod

    calls = _patch_sync(monkeypatch, sync_mod)
    client = MagicMock()
    client.get_workout_count.side_effect = RuntimeError("api down")

    with pytest.raises(RuntimeError):
        sync_mod.full_sync(client)

    assert len(calls["sync_results"]) == 1
    res = calls["sync_results"][0]
    assert res["ok"] is False
    assert "RuntimeError" in res["detail"]
    assert "last_sync" not in calls["sync_states"]


def test_incremental_fallback_records_once(monkeypatch):
    import hevy.sync as sync_mod

    calls = _patch_sync(monkeypatch, sync_mod)
    monkeypatch.setattr(sync_mod, "get_sync_state", lambda k: None)  # forces full_sync fallback
    client = MagicMock()
    client.get_workout_count.return_value = 0
    client.get_workouts.return_value = iter([])
    client.get_exercise_templates.return_value = iter([])
    client.get_body_measurements.return_value = iter([])
    client.get_routines.return_value = iter([])

    sync_mod.incremental_sync(client)

    assert len(calls["sync_results"]) == 1
    assert calls["sync_results"][0]["detail"].startswith("full:")
