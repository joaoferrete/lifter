"""Security-focused tests — permissions, injection prevention, secret hygiene."""

import re
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent


# ── credential file permissions ───────────────────────────────────────────────


def test_fit_token_written_with_mode_600(tmp_path):
    """OAuth token file must be written owner-only (0o600)."""
    import config

    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "fake-refresh-token"}'

    with patch.object(config, "DB_PATH", tmp_path / "db.sqlite"):
        from fit.auth import _write_token

        _write_token(mock_creds)

    token_file = tmp_path / "fit_token.json"
    actual = oct(stat.S_IMODE(token_file.stat().st_mode))
    assert actual == "0o600", f"Expected 0o600, got {actual}"


# ── .gitignore hygiene ────────────────────────────────────────────────────────


def test_gitignore_covers_env():
    content = (PROJECT_ROOT / ".gitignore").read_text()
    assert ".env" in content


def test_gitignore_covers_fit_credentials():
    content = (PROJECT_ROOT / ".gitignore").read_text()
    assert "fit_credentials.json" in content


def test_gitignore_covers_fit_token():
    content = (PROJECT_ROOT / ".gitignore").read_text()
    assert "fit_token.json" in content


def test_gitignore_covers_db_files():
    content = (PROJECT_ROOT / ".gitignore").read_text()
    assert "*.db" in content or "hevy.db" in content


# ── .env.example must contain no real secrets ────────────────────────────────


def test_env_example_has_no_hevy_uuid():
    content = (PROJECT_ROOT / ".env.example").read_text()
    # Real Hevy keys are UUID-shaped
    assert not re.search(r"HEVY_API_KEY=[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", content)


def test_env_example_has_no_gemini_key():
    content = (PROJECT_ROOT / ".env.example").read_text()
    assert not re.search(r"GEMINI_API_KEY=AIza", content)


def test_env_example_has_no_anthropic_key():
    content = (PROJECT_ROOT / ".env.example").read_text()
    assert not re.search(r"ANTHROPIC_API_KEY=sk-ant-[a-zA-Z0-9]", content)


# ── sensitive files must not be tracked by git ───────────────────────────────


def test_env_file_not_tracked_by_git():
    import subprocess

    result = subprocess.run(
        ["git", "ls-files", ".env"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    assert result.stdout.strip() == "", ".env must not be tracked by git"


def test_db_file_not_tracked_by_git():
    import subprocess

    result = subprocess.run(
        ["git", "ls-files", "*.db"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        shell=False,
    )
    assert "hevy.db" not in result.stdout


# ── weeks type guard ──────────────────────────────────────────────────────────


def test_frequency_accepts_string_weeks(tmp_db):
    from analytics.frequency import workout_frequency

    result = workout_frequency("12")
    assert isinstance(result["total_workouts"], int)


def test_frequency_clamps_negative_weeks(tmp_db):
    from analytics.frequency import workout_frequency

    result = workout_frequency(-100)
    assert isinstance(result, dict)


def test_volume_accepts_string_weeks(tmp_db):
    from analytics.volume import weekly_volume

    df = weekly_volume("8")
    assert hasattr(df, "empty")


def test_progression_clamps_negative_weeks(tmp_db):
    from analytics.progression import exercise_progression

    df = exercise_progression("ANY_ID", -5)
    assert df.empty


# ── routine sanitization (injection prevention) ───────────────────────────────


def test_sanitize_strips_all_unknown_exercise_fields():
    from hevy.client import _sanitize_routine

    routine = {
        "title": "Safe Routine",
        "exercises": [
            {
                "exercise_template_id": "ABC",
                "title": "Name",
                "injected_sql": "'; DROP TABLE workouts; --",
                "another_unknown": True,
                "sets": [{"type": "normal", "weight_kg": 80.0, "reps": 5}],
            }
        ],
    }
    result = _sanitize_routine(routine)
    ex = result["exercises"][0]
    allowed = {"exercise_template_id", "superset_id", "rest_seconds", "notes", "sets"}
    assert set(ex.keys()).issubset(allowed), f"Unexpected keys: {set(ex.keys()) - allowed}"


def test_sanitize_strips_unknown_set_fields():
    from hevy.client import _sanitize_routine

    routine = {
        "title": "Test",
        "exercises": [
            {
                "exercise_template_id": "X",
                "sets": [{"type": "normal", "weight_kg": 50.0, "reps": 5, "bad_field": "evil"}],
            }
        ],
    }
    result = _sanitize_routine(routine)
    s = result["exercises"][0]["sets"][0]
    allowed = {"type", "weight_kg", "reps", "distance_meters", "duration_seconds", "custom_metric", "rep_range"}
    assert set(s.keys()).issubset(allowed)


# ── Rich markup injection prevention ─────────────────────────────────────────


def test_rich_escape_handles_markup_in_name():
    """rich.markup.escape() must prefix [ with \\ so Rich treats it as literal text."""
    from rich.markup import escape

    malicious = "[bold red]INJECTED[/bold red]"
    escaped = escape(malicious)
    # escape() converts [ → \[ so Rich does not render it as markup
    assert escaped != malicious, "escape() must modify the string"
    assert "\\[" in escaped, "[ must be backslash-escaped"
    assert "INJECTED" in escaped  # original text still present


# ── is_connected validation ───────────────────────────────────────────────────


def test_is_connected_false_when_no_token(tmp_path):
    import config

    with patch.object(config, "DB_PATH", tmp_path / "missing" / "db.sqlite"):
        from fit.auth import is_connected

        assert is_connected() is False


def test_is_connected_false_when_token_is_garbage(tmp_path):
    import config

    bad_token = tmp_path / "fit_token.json"
    bad_token.write_text("not valid json {{{{")
    with patch.object(config, "DB_PATH", tmp_path / "db.sqlite"):
        from fit.auth import is_connected

        assert is_connected() is False
