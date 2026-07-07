"""Debug logging — writes structured lines to logs/debug-YYYY-MM-DD.log when enabled."""
from datetime import datetime
from pathlib import Path

import paths

LOGS_DIR = paths.LOGS_DIR
RETENTION_MAX_FILES = 14

_enabled: bool = False


def prune_old_logs(max_files: int = RETENTION_MAX_FILES) -> int:
    """Keep only the newest max_files daily log files; delete the rest.

    Count-based (not age-based) so users who don't open the app every day
    still keep their last max_files days of activity. Never raises.
    """
    removed = 0
    try:
        # filename dates are ISO, so lexicographic sort is chronological
        files = sorted(LOGS_DIR.glob("debug-*.log"))
        for f in files[:max(len(files) - max_files, 0)]:
            f.unlink(missing_ok=True)
            removed += 1
    except Exception:
        pass
    return removed


def init() -> None:
    """Read the debug_logging pref and cache the enabled state. Call after init_db()."""
    global _enabled
    try:
        from db.goals import get_pref
        _enabled = get_pref("debug_logging") == "1"
    except Exception:
        _enabled = False
    prune_old_logs()


def enable(on: bool) -> None:
    """Update the in-memory enabled state immediately (no restart needed)."""
    global _enabled
    _enabled = on


def log(category: str, msg: str, **kv) -> None:
    """Append one structured line to today's log file if enabled. Never raises."""
    if not _enabled:
        return
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        extras = "  " + "  ".join(f"{k}={v}" for k, v in kv.items()) if kv else ""
        line = f"{timestamp} [{category:<7}] {msg}{extras}\n"
        log_file = LOGS_DIR / f"debug-{datetime.now().strftime('%Y-%m-%d')}.log"
        with open(log_file, "a") as f:
            f.write(line)
    except Exception:
        pass
