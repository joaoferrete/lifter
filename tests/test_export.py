"""Tests for the db.export data export helper."""

import json

import pytest

from db.export import export_data
from db.goals import save_goal
from db.memories import save_memory


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_full_export_on_empty_db(tmp_db, tmp_path):
    path, rows = export_data("full", dest_dir=tmp_path / "out")

    assert path.exists()
    assert rows == 0
    payload = _read(path)
    assert payload["app"] == "lifter"
    assert payload["kind"] == "full"
    assert "exported_at" in payload
    # Every schema table is present, all empty
    for table in ("workouts", "chat_memories", "user_goals", "body_measurements"):
        assert payload["tables"][table] == []
    assert payload["token_usage"]["lifetime"] == {"input": 0, "output": 0, "cache_read": 0}


def test_memories_export(tmp_db, tmp_path):
    save_memory("Prefers dumbbell over barbell pressing")
    save_memory("Left shoulder impingement — avoid overhead work")

    path, rows = export_data("memories", dest_dir=tmp_path / "out")

    assert rows == 2
    payload = _read(path)
    assert payload["kind"] == "memories"
    summaries = [r["summary"] for r in payload["tables"]["chat_memories"]]
    assert "Prefers dumbbell over barbell pressing" in summaries
    assert "token_usage" not in payload


def test_goals_export_includes_token_usage(tmp_db, tmp_path):
    save_goal(type="lift", description="Bench 100kg", target=100, unit="kg")

    path, rows = export_data("goals", dest_dir=tmp_path / "out")

    assert rows == 1
    payload = _read(path)
    assert payload["tables"]["user_goals"][0]["description"] == "Bench 100kg"
    assert set(payload["token_usage"]) == {"lifetime", "month"}


def test_export_creates_nested_dest_dir(tmp_db, tmp_path):
    dest = tmp_path / "deeply" / "nested" / "exports"
    path, _rows = export_data("measurements", dest_dir=dest)

    assert dest.is_dir()
    assert path.parent == dest


def test_export_filename_pattern(tmp_db, tmp_path):
    path, _rows = export_data("memories", dest_dir=tmp_path)

    assert path.name.startswith("lifter-export-memories-")
    assert path.suffix == ".json"


def test_export_unknown_kind_raises(tmp_db, tmp_path):
    with pytest.raises(KeyError):
        export_data("nope", dest_dir=tmp_path)


def test_export_honors_export_dir_override(tmp_db, tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "EXPORT_DIR", str(tmp_path / "custom-exports"))

    path, _rows = export_data("full")

    assert path.parent == tmp_path / "custom-exports"
    assert path.exists()


def test_export_defaults_next_to_db(tmp_db, monkeypatch):
    import config

    monkeypatch.setattr(config, "EXPORT_DIR", "")

    path, _rows = export_data("full")

    assert path.parent == config.DB_PATH.parent / "exports"
    assert path.exists()
