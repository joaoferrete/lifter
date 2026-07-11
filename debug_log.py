"""Debug logging — writes structured lines to logs/debug-YYYY-MM-DD.log when enabled."""

from datetime import datetime
from pathlib import Path

import paths

LOGS_DIR = paths.LOGS_DIR  # default; the LOGS_DIR .env key (config) overrides
RETENTION_MAX_FILES = 14


def logs_dir() -> Path:
    """Effective logs directory: the LOGS_DIR .env override when it's a usable
    absolute path, else the XDG default. Resolved lazily so Settings changes
    apply without a restart. Never raises."""
    try:
        import config

        raw = getattr(config, "LOGS_DIR", "")
        if raw:
            p = Path(raw).expanduser()
            if p.is_absolute():
                return p
    except Exception:
        pass
    return LOGS_DIR


_enabled: bool = False


def prune_old_logs(max_files: int = RETENTION_MAX_FILES) -> int:
    """Keep only the newest max_files daily log files; delete the rest.

    Count-based (not age-based) so users who don't open the app every day
    still keep their last max_files days of activity. Never raises.
    """
    removed = 0
    try:
        # filename dates are ISO, so lexicographic sort is chronological
        files = sorted(logs_dir().glob("debug-*.log"))
        for f in files[: max(len(files) - max_files, 0)]:
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


def log(category: str, msg: str, **kv: object) -> None:
    """Append one structured line to today's log file if enabled. Never raises."""
    if not _enabled:
        return
    _write_line(category, msg, kv)


def error(category: str, msg: str, exc: BaseException | None = None, **kv: object) -> None:
    """Append an error line (with full traceback when `exc` is given).

    Unlike log(), always writes regardless of the debug_logging pref — a crash
    record is the one thing worth having after the fact. Never raises.
    """
    _write_line(category, msg, kv, extra_block=_traceback_of(exc))


def _traceback_of(exc: BaseException | None) -> str:
    if exc is None:
        return ""
    try:
        import traceback

        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        return "".join(f"    | {line}\n" for line in tb.rstrip().splitlines())
    except Exception:
        return ""


def _write_line(category: str, msg: str, kv: dict[str, object] | None = None, extra_block: str = "") -> None:
    try:
        target_dir = logs_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        extras = "  " + "  ".join(f"{k}={v}" for k, v in kv.items()) if kv else ""
        line = f"{timestamp} [{category:<7}] {msg}{extras}\n{extra_block}"
        log_file = target_dir / f"debug-{datetime.now().strftime('%Y-%m-%d')}.log"
        with open(log_file, "a") as f:
            f.write(line)
    except Exception:
        pass
