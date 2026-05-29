"""Tests for the Hevy API client — payload sanitization and mocked HTTP calls."""
import pytest
from unittest.mock import MagicMock, patch


# ── _sanitize_routine ─────────────────────────────────────────────────────────

def test_sanitize_removes_title_from_exercises():
    from hevy.client import _sanitize_routine
    routine = {
        "title": "Push Day",
        "exercises": [{
            "exercise_template_id": "ABC123",
            "title": "Bench Press",      # must be stripped
            "rest_seconds": 90,
            "sets": [{"type": "normal", "weight_kg": 80.0, "reps": 5}],
        }],
    }
    result = _sanitize_routine(routine)
    assert "title" not in result["exercises"][0]
    assert result["exercises"][0]["exercise_template_id"] == "ABC123"
    assert result["exercises"][0]["rest_seconds"] == 90


def test_sanitize_drops_null_set_fields():
    from hevy.client import _sanitize_routine
    routine = {
        "title": "Test",
        "exercises": [{
            "exercise_template_id": "ABC",
            "sets": [{"type": "warmup", "weight_kg": None, "reps": 10, "distance_meters": None}],
        }],
    }
    result = _sanitize_routine(routine)
    s = result["exercises"][0]["sets"][0]
    assert "weight_kg" not in s
    assert "distance_meters" not in s
    assert s["type"] == "warmup"
    assert s["reps"] == 10


def test_sanitize_strips_unknown_exercise_fields():
    from hevy.client import _sanitize_routine
    routine = {
        "title": "Test",
        "exercises": [{
            "exercise_template_id": "ABC",
            "unknown_field": "DROP TABLE workouts;",
            "sets": [{"type": "normal", "weight_kg": 50.0, "reps": 5}],
        }],
    }
    result = _sanitize_routine(routine)
    assert "unknown_field" not in result["exercises"][0]


def test_sanitize_preserves_notes_and_rest():
    from hevy.client import _sanitize_routine
    routine = {
        "title": "Push",
        "notes": "Focus on form",
        "exercises": [{
            "exercise_template_id": "ABC",
            "rest_seconds": 120,
            "notes": "Elbows in",
            "sets": [{"type": "normal", "weight_kg": 60.0, "reps": 8}],
        }],
    }
    result = _sanitize_routine(routine)
    assert result["notes"] == "Focus on form"
    ex = result["exercises"][0]
    assert ex["rest_seconds"] == 120
    assert ex["notes"] == "Elbows in"


def test_sanitize_empty_exercises():
    from hevy.client import _sanitize_routine
    result = _sanitize_routine({"title": "Empty", "exercises": []})
    assert result["exercises"] == []


# ── mocked API calls ──────────────────────────────────────────────────────────

def test_get_workout_count():
    import httpx
    from hevy.client import HevyClient

    mock_response = MagicMock()
    mock_response.json.return_value = {"workout_count": 42}
    mock_response.raise_for_status = MagicMock()

    with patch.object(httpx, "get", return_value=mock_response):
        client = HevyClient(api_key="fake-key")
        count = client.get_workout_count()

    assert count == 42


def test_get_user_info():
    import httpx
    from hevy.client import HevyClient

    mock_response = MagicMock()
    mock_response.json.return_value = {"data": {"id": "u1", "name": "Test User", "url": "https://hevy.com/test"}}
    mock_response.raise_for_status = MagicMock()

    with patch.object(httpx, "get", return_value=mock_response):
        client = HevyClient(api_key="fake-key")
        info = client.get_user_info()

    assert info["name"] == "Test User"


def test_create_routine_sanitizes_payload():
    """create_routine must call _sanitize_routine before posting."""
    import httpx
    from hevy.client import HevyClient

    posted_body = {}

    def capture_post(url, **kwargs):
        posted_body.update(kwargs.get("json", {}))
        mock = MagicMock()
        mock.json.return_value = {"routine": {"id": "new-routine-id"}}
        mock.raise_for_status = MagicMock()
        return mock

    with patch.object(httpx, "post", side_effect=capture_post):
        client = HevyClient(api_key="fake-key")
        client.create_routine({
            "title": "Test",
            "exercises": [{
                "exercise_template_id": "ABC",
                "title": "Should be stripped",
                "sets": [{"type": "normal", "weight_kg": 80.0, "reps": 5}],
            }],
        })

    exercises = posted_body.get("routine", {}).get("exercises", [])
    assert len(exercises) == 1
    assert "title" not in exercises[0], "title must be stripped by sanitize"
