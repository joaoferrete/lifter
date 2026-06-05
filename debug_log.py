"""Debug logging — writes structured lines to logs/debug-YYYY-MM-DD.log when enabled."""
from datetime import datetime
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent
LOGS_DIR = _PROJECT_DIR / "logs"

_enabled: bool = False


def init() -> None:
    """Read the debug_logging pref and cache the enabled state. Call after init_db()."""
    global _enabled
    try:
        from db.goals import get_pref
        _enabled = get_pref("debug_logging") == "1"
    except Exception:
        _enabled = False


def enable(on: bool) -> None:
    """Update the in-memory enabled state immediately (no restart needed)."""
    global _enabled
    _enabled = on


def log(category: str, msg: str, **kv) -> None:
    """Append one structured line to today's log file if enabled. Never raises."""
    if not _enabled:
        return
    try:
        LOGS_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        extras = "  " + "  ".join(f"{k}={v}" for k, v in kv.items()) if kv else ""
        line = f"{timestamp} [{category:<7}] {msg}{extras}\n"
        log_file = LOGS_DIR / f"debug-{datetime.now().strftime('%Y-%m-%d')}.log"
        with open(log_file, "a") as f:
            f.write(line)
    except Exception:
        pass
