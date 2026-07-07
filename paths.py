"""Writable-path resolution (XDG) and one-time legacy-layout migration.

Lifter used to keep everything next to its own source files. That breaks
once the package is installed into a read-only site-packages, so all
writable data now lives in user directories:

    data   (profiles, databases)  ~/.local/share/lifter   $XDG_DATA_HOME
    config (.env, credentials)    ~/.config/lifter        $XDG_CONFIG_HOME
    state  (logs, chat history)   ~/.local/state/lifter   $XDG_STATE_HOME

Setting LIFTER_HOME forces all three into a single directory (useful for
tests, portable installs, and development).
"""
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

_APP = "lifter"
_LEGACY_DIR = Path(__file__).resolve().parent   # old install layout (repo/site-packages)


@dataclass(frozen=True)
class Dirs:
    data: Path
    config: Path
    state: Path


def resolve_dirs(environ: Mapping[str, str] | None = None) -> Dirs:
    env = os.environ if environ is None else environ
    home_override = env.get("LIFTER_HOME")
    if home_override:
        base = Path(home_override).expanduser()
        return Dirs(base, base, base)

    def xdg(var: str, default: str) -> Path:
        raw = env.get(var, "")
        # the XDG spec says relative values must be ignored
        base = Path(raw).expanduser() if raw and Path(raw).expanduser().is_absolute() else Path.home() / default
        return base / _APP

    return Dirs(
        data=xdg("XDG_DATA_HOME", ".local/share"),
        config=xdg("XDG_CONFIG_HOME", ".config"),
        state=xdg("XDG_STATE_HOME", ".local/state"),
    )


_DIRS = resolve_dirs()
DATA_DIR = _DIRS.data
CONFIG_DIR = _DIRS.config
STATE_DIR = _DIRS.state

PROFILES_DIR = DATA_DIR / "profiles"
PROFILES_FILE = DATA_DIR / "profiles.json"
ENV_FILE = CONFIG_DIR / ".env"
FIT_CREDENTIALS_FILE = CONFIG_DIR / "fit_credentials.json"
LOGS_DIR = STATE_DIR / "logs"
CHAT_HISTORY_FILE = STATE_DIR / "chat_history"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)  # mode applies only on create
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _migration_items() -> list[tuple[Path, Path, bool]]:
    """(src, dst, is_secret) pairs, resolved lazily so tests can monkeypatch."""
    return [
        (_LEGACY_DIR / "profiles", PROFILES_DIR, False),
        (_LEGACY_DIR / "profiles.json", PROFILES_FILE, False),
        (_LEGACY_DIR / ".env", ENV_FILE, True),
        (_LEGACY_DIR / "fit_credentials.json", FIT_CREDENTIALS_FILE, True),
        (_LEGACY_DIR / "logs", LOGS_DIR, False),
        (Path.home() / ".hevy_chat_history", CHAT_HISTORY_FILE, False),
    ]


def migrate_legacy_layout() -> list[str]:
    """Move legacy project-dir data to the user dirs. Idempotent, never raises.

    Each item is moved independently — a failure leaves that item in place to
    be retried on the next run (the guard is `src exists and dst missing`).
    Returns human-readable "src -> dst" strings for what actually moved.
    """
    ensure_dirs()
    moved: list[str] = []
    for src, dst, is_secret in _migration_items():
        try:
            if not src.exists() or dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            if is_secret:
                try:
                    dst.chmod(0o600)
                except OSError:
                    pass
            moved.append(f"{src} -> {dst}")
        except OSError:
            continue
    return moved
