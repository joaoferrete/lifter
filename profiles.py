"""Profile management for multi-user support."""
import json
import re
import shutil
from pathlib import Path

import config

_PROJECT_DIR = Path(__file__).resolve().parent
PROFILES_DIR  = _PROJECT_DIR / "profiles"
PROFILES_FILE = _PROJECT_DIR / "profiles.json"


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]", "-", name.lower()).strip("-")
    return slug or "profile"


def _read() -> dict:
    if not PROFILES_FILE.exists():
        return {"profiles": [], "active": None}
    try:
        return json.loads(PROFILES_FILE.read_text())
    except Exception:
        return {"profiles": [], "active": None}


def _write(data: dict) -> None:
    PROFILES_FILE.write_text(json.dumps(data, indent=2))


def list_profiles() -> list[dict]:
    return _read().get("profiles", [])


def get_active_slug() -> str | None:
    return _read().get("active")


def set_active_slug(slug: str) -> None:
    data = _read()
    data["active"] = slug
    _write(data)


def create_profile(name: str, hevy_api_key: str = "") -> dict:
    """Create a new profile directory and profile.json. Returns the profile dict."""
    slug = _slug(name)
    existing = {p["slug"] for p in list_profiles()}
    base, i = slug, 2
    while slug in existing:
        slug = f"{base}-{i}"
        i += 1

    profile_dir = PROFILES_DIR / slug
    profile_dir.mkdir(parents=True, exist_ok=True)

    profile_cfg = {"name": name, "slug": slug, "hevy_api_key": hevy_api_key}
    (profile_dir / "profile.json").write_text(json.dumps(profile_cfg, indent=2))

    data = _read()
    data["profiles"].append({"slug": slug, "name": name})
    if not data.get("active"):
        data["active"] = slug
    _write(data)
    return profile_cfg


def rename_profile(slug: str, new_name: str) -> None:
    cfg_file = PROFILES_DIR / slug / "profile.json"
    if cfg_file.exists():
        cfg = json.loads(cfg_file.read_text())
        cfg["name"] = new_name
        cfg_file.write_text(json.dumps(cfg, indent=2))

    data = _read()
    for p in data["profiles"]:
        if p["slug"] == slug:
            p["name"] = new_name
    _write(data)


def delete_profile(slug: str) -> None:
    profile_dir = PROFILES_DIR / slug
    if profile_dir.exists():
        shutil.rmtree(profile_dir)

    data = _read()
    data["profiles"] = [p for p in data["profiles"] if p["slug"] != slug]
    if data.get("active") == slug:
        data["active"] = data["profiles"][0]["slug"] if data["profiles"] else None
    _write(data)


def activate_profile(slug: str) -> None:
    """Point config globals at this profile's paths and API key."""
    profile_dir = PROFILES_DIR / slug
    profile_dir.mkdir(parents=True, exist_ok=True)
    config.DB_PATH = profile_dir / "hevy.db"

    cfg_file = profile_dir / "profile.json"
    if cfg_file.exists():
        try:
            key = json.loads(cfg_file.read_text()).get("hevy_api_key", "")
            if key:
                config.HEVY_API_KEY = key
        except Exception:
            pass


def get_profile_name(slug: str) -> str:
    for p in list_profiles():
        if p["slug"] == slug:
            return p["name"]
    return slug


def update_profile_key(slug: str, hevy_api_key: str) -> None:
    """Update the Hevy API key stored in a profile's profile.json."""
    cfg_file = PROFILES_DIR / slug / "profile.json"
    if cfg_file.exists():
        cfg = json.loads(cfg_file.read_text())
    else:
        cfg = {"name": get_profile_name(slug), "slug": slug}
    cfg["hevy_api_key"] = hevy_api_key
    cfg_file.write_text(json.dumps(cfg, indent=2))
