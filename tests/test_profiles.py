"""Tests for profiles.py — profile management module."""

import json

import pytest


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Redirect profiles module globals to tmp_path so tests never touch the real project."""
    import profile_mgr

    monkeypatch.setattr(profile_mgr, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(profile_mgr, "PROFILES_FILE", tmp_path / "profiles.json")


# ── list_profiles ─────────────────────────────────────────────────────────────


def test_list_profiles_empty_when_no_file():
    from profile_mgr import list_profiles

    assert list_profiles() == []


def test_list_profiles_returns_all():
    from profile_mgr import create_profile, list_profiles

    create_profile("Alice")
    create_profile("Bob")
    slugs = [p["slug"] for p in list_profiles()]
    assert "alice" in slugs
    assert "bob" in slugs


# ── create_profile ────────────────────────────────────────────────────────────


def test_create_profile_creates_directory(tmp_path):
    from profile_mgr import PROFILES_DIR, create_profile

    p = create_profile("Alice")
    assert (PROFILES_DIR / p["slug"]).is_dir()


def test_create_profile_writes_profile_json(tmp_path):
    from profile_mgr import PROFILES_DIR, create_profile

    p = create_profile("Alice", hevy_api_key="key123")
    cfg_file = PROFILES_DIR / p["slug"] / "profile.json"
    assert cfg_file.exists()
    data = json.loads(cfg_file.read_text())
    assert data["name"] == "Alice"
    assert data["hevy_api_key"] == "key123"


def test_create_profile_unique_slug_for_duplicate_name():
    from profile_mgr import create_profile

    p1 = create_profile("Alice")
    p2 = create_profile("Alice")
    assert p1["slug"] != p2["slug"]
    assert p2["slug"].startswith(p1["slug"])


def test_create_profile_sets_active_if_none():
    from profile_mgr import create_profile, get_active_slug

    p = create_profile("Solo")
    assert get_active_slug() == p["slug"]


def test_create_profile_does_not_override_existing_active():
    from profile_mgr import create_profile, get_active_slug, set_active_slug

    p1 = create_profile("First")
    set_active_slug(p1["slug"])
    create_profile("Second")
    assert get_active_slug() == p1["slug"]


# ── get/set active slug ───────────────────────────────────────────────────────


def test_get_active_slug_round_trip():
    from profile_mgr import create_profile, get_active_slug, set_active_slug

    p = create_profile("Test")
    set_active_slug(p["slug"])
    assert get_active_slug() == p["slug"]


def test_get_active_slug_none_when_no_file():
    from profile_mgr import get_active_slug

    assert get_active_slug() is None


# ── activate_profile ──────────────────────────────────────────────────────────


def test_activate_profile_updates_config_db_path(tmp_path):
    import config
    from profile_mgr import PROFILES_DIR, activate_profile, create_profile

    p = create_profile("Alice")
    activate_profile(p["slug"])
    assert PROFILES_DIR / p["slug"] / "hevy.db" == config.DB_PATH


def test_activate_profile_updates_hevy_api_key():
    import config
    from profile_mgr import activate_profile, create_profile

    p = create_profile("Alice", hevy_api_key="secret-key")
    activate_profile(p["slug"])
    assert config.HEVY_API_KEY == "secret-key"


def test_activate_profile_no_key_leaves_config_unchanged():
    import config

    original_key = config.HEVY_API_KEY
    from profile_mgr import activate_profile, create_profile

    p = create_profile("NoKey")
    activate_profile(p["slug"])
    assert original_key == config.HEVY_API_KEY


# ── rename_profile ────────────────────────────────────────────────────────────


def test_rename_profile_updates_name(tmp_path):
    from profile_mgr import PROFILES_DIR, create_profile, list_profiles, rename_profile

    p = create_profile("OldName")
    rename_profile(p["slug"], "NewName")
    names = [x["name"] for x in list_profiles()]
    assert "NewName" in names
    assert "OldName" not in names
    cfg = json.loads((PROFILES_DIR / p["slug"] / "profile.json").read_text())
    assert cfg["name"] == "NewName"


# ── delete_profile ────────────────────────────────────────────────────────────


def test_delete_profile_removes_directory(tmp_path):
    from profile_mgr import PROFILES_DIR, create_profile, delete_profile

    p = create_profile("ToDelete")
    delete_profile(p["slug"])
    assert not (PROFILES_DIR / p["slug"]).exists()


def test_delete_profile_removes_entry_from_profiles_json():
    from profile_mgr import create_profile, delete_profile, list_profiles

    p = create_profile("ToDelete")
    delete_profile(p["slug"])
    assert all(x["slug"] != p["slug"] for x in list_profiles())


def test_delete_active_profile_reassigns_active():
    from profile_mgr import create_profile, delete_profile, get_active_slug, set_active_slug

    p1 = create_profile("One")
    p2 = create_profile("Two")
    set_active_slug(p1["slug"])
    delete_profile(p1["slug"])
    assert get_active_slug() == p2["slug"]


# ── get_profile_name ──────────────────────────────────────────────────────────


def test_get_profile_name_returns_name():
    from profile_mgr import create_profile, get_profile_name

    p = create_profile("FriendlyName")
    assert get_profile_name(p["slug"]) == "FriendlyName"


def test_get_profile_name_returns_slug_when_not_found():
    from profile_mgr import get_profile_name

    assert get_profile_name("unknown-slug") == "unknown-slug"


# ── update_profile_key ────────────────────────────────────────────────────────


def test_update_profile_key(tmp_path):
    from profile_mgr import PROFILES_DIR, create_profile, update_profile_key

    p = create_profile("Alice")
    update_profile_key(p["slug"], "new-key-abc")
    cfg = json.loads((PROFILES_DIR / p["slug"] / "profile.json").read_text())
    assert cfg["hevy_api_key"] == "new-key-abc"


# ── corrupt profile.json resilience ───────────────────────────────────────────


def test_rename_profile_survives_corrupt_profile_json():
    from profile_mgr import PROFILES_DIR, create_profile, get_profile_name, rename_profile

    p = create_profile("Alice")
    cfg_file = PROFILES_DIR / p["slug"] / "profile.json"
    cfg_file.write_text('{"name": "Alice", TRUNCATED')

    rename_profile(p["slug"], "Alicia")

    data = json.loads(cfg_file.read_text())
    assert data["name"] == "Alicia"
    assert get_profile_name(p["slug"]) == "Alicia"


def test_update_profile_key_survives_corrupt_profile_json():
    from profile_mgr import PROFILES_DIR, create_profile, update_profile_key

    p = create_profile("Bob", hevy_api_key="old")
    cfg_file = PROFILES_DIR / p["slug"] / "profile.json"
    cfg_file.write_text("not json at all")

    update_profile_key(p["slug"], "new-key")

    data = json.loads(cfg_file.read_text())
    assert data["hevy_api_key"] == "new-key"
    assert data["slug"] == p["slug"]


def test_write_is_atomic_no_tmp_left_behind():
    from profile_mgr import PROFILES_FILE, create_profile

    create_profile("Carol")
    assert PROFILES_FILE.exists()
    assert not PROFILES_FILE.with_suffix(".json.tmp").exists()
