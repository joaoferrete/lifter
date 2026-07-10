"""Profile management for multi-user support."""

import json
import os
import re
import shutil

import config
import paths

PROFILES_DIR = paths.PROFILES_DIR
PROFILES_FILE = paths.PROFILES_FILE


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
    try:
        PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace so a failed write can't leave a corrupt profiles.json.
        tmp = PROFILES_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, PROFILES_FILE)
    except OSError as e:
        raise RuntimeError(f"Could not save profiles file at {PROFILES_FILE}: {e}") from e


def _read_profile_cfg(slug: str) -> dict:
    """Read a profile's profile.json, rebuilding a minimal one if corrupt."""
    cfg_file = PROFILES_DIR / slug / "profile.json"
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text())
            if isinstance(cfg, dict):
                return cfg
        except (OSError, json.JSONDecodeError) as e:
            try:
                import debug_log

                debug_log.error("PROFILE", "profile.json corrupt — rebuilding", slug=slug, error=str(e)[:200])
            except Exception:
                pass
    return {"name": get_profile_name(slug), "slug": slug}


def _write_profile_cfg(slug: str, cfg: dict) -> None:
    cfg_file = PROFILES_DIR / slug / "profile.json"
    try:
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cfg_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2))
        os.replace(tmp, cfg_file)
    except OSError as e:
        raise RuntimeError(f"Could not save profile file at {cfg_file}: {e}") from e


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
    try:
        from debug_log import log

        log("PROFILE", "Profile created", name=name, slug=slug)
    except Exception:
        pass
    return profile_cfg


def rename_profile(slug: str, new_name: str) -> None:
    if (PROFILES_DIR / slug / "profile.json").exists():
        cfg = _read_profile_cfg(slug)
        cfg["name"] = new_name
        _write_profile_cfg(slug, cfg)

    data = _read()
    old_name = next((p["name"] for p in data["profiles"] if p["slug"] == slug), slug)
    for p in data["profiles"]:
        if p["slug"] == slug:
            p["name"] = new_name
    _write(data)
    try:
        from debug_log import log

        log("PROFILE", "Profile renamed", slug=slug, old=old_name, new=new_name)
    except Exception:
        pass


def delete_profile(slug: str) -> None:
    profile_dir = PROFILES_DIR / slug
    if profile_dir.exists():
        shutil.rmtree(profile_dir)

    name = get_profile_name(slug)
    data = _read()
    data["profiles"] = [p for p in data["profiles"] if p["slug"] != slug]
    if data.get("active") == slug:
        data["active"] = data["profiles"][0]["slug"] if data["profiles"] else None
    _write(data)
    try:
        from debug_log import log

        log("PROFILE", "Profile deleted", slug=slug, name=name)
    except Exception:
        pass


def activate_profile(slug: str) -> None:
    """Point config globals at this profile's paths and API key."""
    profile_dir = PROFILES_DIR / slug
    profile_dir.mkdir(parents=True, exist_ok=True)
    config.PROFILE_DB_PATH = profile_dir / "hevy.db"
    config.DB_PATH = config.PROFILE_DB_PATH

    cfg_file = profile_dir / "profile.json"
    if cfg_file.exists():
        try:
            key = json.loads(cfg_file.read_text()).get("hevy_api_key", "")
            if key:
                config.HEVY_API_KEY = key
        except Exception:
            pass
    try:
        from debug_log import log

        log("PROFILE", "Profile activated", slug=slug)
    except Exception:
        pass


def get_profile_name(slug: str) -> str:
    for p in list_profiles():
        if p["slug"] == slug:
            return p["name"]
    return slug


def update_profile_key(slug: str, hevy_api_key: str) -> None:
    """Update the Hevy API key stored in a profile's profile.json."""
    cfg = _read_profile_cfg(slug)
    cfg["hevy_api_key"] = hevy_api_key
    _write_profile_cfg(slug, cfg)
