"""Tests for the one-time legacy-layout migration (paths.migrate_legacy_layout)."""

import os
import stat

import pytest

import paths


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point every paths constant plus the legacy dir and home at tmp_path."""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "xdg" / "data"
    config = tmp_path / "xdg" / "config"
    state = tmp_path / "xdg" / "state"

    monkeypatch.setattr(paths, "_LEGACY_DIR", legacy)
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "CONFIG_DIR", config)
    monkeypatch.setattr(paths, "STATE_DIR", state)
    monkeypatch.setattr(paths, "PROFILES_DIR", data / "profiles")
    monkeypatch.setattr(paths, "PROFILES_FILE", data / "profiles.json")
    monkeypatch.setattr(paths, "ENV_FILE", config / ".env")
    monkeypatch.setattr(paths, "FIT_CREDENTIALS_FILE", config / "fit_credentials.json")
    monkeypatch.setattr(paths, "LOGS_DIR", state / "logs")
    monkeypatch.setattr(paths, "CHAT_HISTORY_FILE", state / "chat_history")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: home))
    return legacy, home


def _populate_legacy(legacy, home):
    (legacy / "profiles" / "default").mkdir(parents=True)
    (legacy / "profiles" / "default" / "hevy.db").write_text("db")
    (legacy / "profiles.json").write_text('{"profiles": []}')
    (legacy / ".env").write_text("GEMINI_API_KEY=abc\n")
    (legacy / "fit_credentials.json").write_text("{}")
    (legacy / "logs").mkdir()
    (legacy / "logs" / "debug-2026-01-01.log").write_text("x")
    (home / ".hevy_chat_history").write_text("hist")


def test_happy_path_moves_all_items(sandbox):
    legacy, home = sandbox
    _populate_legacy(legacy, home)

    moved = paths.migrate_legacy_layout()

    assert len(moved) == 6
    assert (paths.PROFILES_DIR / "default" / "hevy.db").read_text() == "db"
    assert paths.PROFILES_FILE.exists()
    assert paths.ENV_FILE.read_text() == "GEMINI_API_KEY=abc\n"
    assert paths.FIT_CREDENTIALS_FILE.exists()
    assert (paths.LOGS_DIR / "debug-2026-01-01.log").exists()
    assert paths.CHAT_HISTORY_FILE.read_text() == "hist"
    # sources gone
    assert not (legacy / "profiles").exists()
    assert not (home / ".hevy_chat_history").exists()


def test_idempotent_second_run(sandbox):
    legacy, home = sandbox
    _populate_legacy(legacy, home)
    assert paths.migrate_legacy_layout()
    assert paths.migrate_legacy_layout() == []


def test_nothing_to_migrate(sandbox):
    assert paths.migrate_legacy_layout() == []


def test_existing_destination_is_never_overwritten(sandbox):
    legacy, home = sandbox
    (legacy / "profiles.json").write_text("OLD")
    paths.PROFILES_FILE.parent.mkdir(parents=True)
    paths.PROFILES_FILE.write_text("NEW")

    moved = paths.migrate_legacy_layout()

    assert moved == []
    assert paths.PROFILES_FILE.read_text() == "NEW"
    assert (legacy / "profiles.json").read_text() == "OLD"


def test_secret_files_end_up_0600(sandbox):
    legacy, home = sandbox
    (legacy / ".env").write_text("KEY=v\n")
    os.chmod(legacy / ".env", 0o644)

    paths.migrate_legacy_layout()

    mode = stat.S_IMODE(paths.ENV_FILE.stat().st_mode)
    assert mode == 0o600


def test_one_failure_does_not_block_others(sandbox, monkeypatch):
    legacy, home = sandbox
    _populate_legacy(legacy, home)

    import shutil as _shutil

    orig_move = _shutil.move

    def flaky_move(src, dst):
        if src.endswith("profiles.json"):
            raise OSError("simulated failure")
        return orig_move(src, dst)

    monkeypatch.setattr(paths.shutil, "move", flaky_move)
    moved = paths.migrate_legacy_layout()

    assert len(moved) == 5
    assert (legacy / "profiles.json").exists()  # left for retry
    assert paths.ENV_FILE.exists()  # others proceeded

    # retry with move restored picks up the leftover
    monkeypatch.setattr(paths.shutil, "move", orig_move)
    assert len(paths.migrate_legacy_layout()) == 1
