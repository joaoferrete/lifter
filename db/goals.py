"""User goals and preferences management."""

import calendar
import sqlite3
from datetime import UTC, date, datetime

from db.store import connect as _conn
from db.store import transaction as _tx


def _invalidate_render_cache() -> None:
    """Drop memoized render data after a goal mutation (see render_cache)."""
    from render_cache import invalidate

    invalidate()


# ── preferences ───────────────────────────────────────────────────────────────


def get_pref(key: str) -> str | None:
    with _tx(_conn()) as conn:
        row = conn.execute("SELECT value FROM user_preferences WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_pref(key: str, value: str) -> None:
    with _tx(_conn()) as conn:
        conn.execute(
            "INSERT INTO user_preferences (key, value, updated_at) VALUES (?, ?, datetime('now'))"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value),
        )


# ── token usage tracking ──────────────────────────────────────────────────────

# Lifetime (never auto-reset) counters.
_TOKEN_KEYS = ("ai_tokens_input", "ai_tokens_output", "ai_tokens_cache_read")
# Current-month counters — wiped automatically when the configured reset day passes.
_TOKEN_MONTH_KEYS = ("ai_tokens_month_input", "ai_tokens_month_output", "ai_tokens_month_cache_read")

_PERIOD_START_KEY = "ai_tokens_period_start"
_RESET_DAY_KEY = "ai_tokens_reset_day"
_DEFAULT_RESET_DAY = 1


def get_token_reset_day() -> int:
    """Return the configured day-of-month (1–31) on which monthly tokens reset."""
    raw = get_pref(_RESET_DAY_KEY)
    try:
        day = int(raw) if raw else _DEFAULT_RESET_DAY
    except (TypeError, ValueError):
        return _DEFAULT_RESET_DAY
    return min(31, max(1, day))


def set_token_reset_day(day: int) -> None:
    """Persist the monthly reset day, clamped to 1–31."""
    set_pref(_RESET_DAY_KEY, str(min(31, max(1, int(day)))))


def _current_period_start(reset_day: int, today: date) -> date:
    """The most recent occurrence of `reset_day` on or before `today`.

    `reset_day` is clamped per-month, so day 31 lands on the last day of short
    months (e.g. 28 Feb)."""
    this_month_day = min(reset_day, calendar.monthrange(today.year, today.month)[1])
    if today.day >= this_month_day:
        return date(today.year, today.month, this_month_day)
    # Period started in the previous month.
    year = today.year - 1 if today.month == 1 else today.year
    month = 12 if today.month == 1 else today.month - 1
    prev_day = min(reset_day, calendar.monthrange(year, month)[1])
    return date(year, month, prev_day)


def _maybe_rollover_month(conn: sqlite3.Connection) -> None:
    """Wipe the month counters if we've crossed into a new period since last seen.

    Operates on an already-open connection. Never raises on logging failure."""
    reset_day = get_token_reset_day()
    today = datetime.now(UTC).astimezone().date()
    period_start = _current_period_start(reset_day, today).isoformat()

    row = conn.execute("SELECT value FROM user_preferences WHERE key = ?", (_PERIOD_START_KEY,)).fetchone()
    stored = row["value"] if row else None

    if stored == period_start:
        return

    if stored is not None:
        try:
            from debug_log import log

            log(
                "TOKENS",
                "monthly counters rolled over",
                old_period=stored,
                new_period=period_start,
                reset_day=reset_day,
            )
        except Exception:
            pass
        conn.execute(
            "DELETE FROM user_preferences WHERE key IN (?,?,?)",
            _TOKEN_MONTH_KEYS,
        )

    conn.execute(
        "INSERT INTO user_preferences (key, value, updated_at) VALUES (?, ?, datetime('now'))"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (_PERIOD_START_KEY, period_start),
    )


def maybe_rollover_tokens() -> None:
    """Public entry point — run the monthly rollover check (e.g. at app startup)."""
    with _tx(_conn()) as conn:
        _maybe_rollover_month(conn)


def add_token_usage(input_tokens: int = 0, output_tokens: int = 0, cache_read_tokens: int = 0) -> None:
    """Atomically increment both the lifetime and current-month token counters."""
    pairs = [
        ("ai_tokens_input", input_tokens),
        ("ai_tokens_output", output_tokens),
        ("ai_tokens_cache_read", cache_read_tokens),
        ("ai_tokens_month_input", input_tokens),
        ("ai_tokens_month_output", output_tokens),
        ("ai_tokens_month_cache_read", cache_read_tokens),
    ]
    with _tx(_conn()) as conn:
        _maybe_rollover_month(conn)
        for key, val in pairs:
            if val:
                conn.execute(
                    """INSERT INTO user_preferences (key, value, updated_at)
                       VALUES (?, ?, datetime('now'))
                       ON CONFLICT(key) DO UPDATE SET
                         value      = CAST(CAST(value AS INTEGER) + ? AS TEXT),
                         updated_at = datetime('now')""",
                    (key, str(val), val),
                )


def _read_counters(conn: sqlite3.Connection, keys: tuple[str, ...]) -> dict[str, int]:
    rows = {
        r["key"]: int(r["value"] or 0)
        for r in conn.execute(
            f"SELECT key, value FROM user_preferences WHERE key IN ({','.join('?' * len(keys))})",
            keys,
        ).fetchall()
    }
    return rows


def get_token_usage() -> dict:
    """Return lifetime token usage counters as {input, output, cache_read}."""
    with _tx(_conn()) as conn:
        _maybe_rollover_month(conn)
        rows = _read_counters(conn, _TOKEN_KEYS)
    return {
        "input": rows.get("ai_tokens_input", 0),
        "output": rows.get("ai_tokens_output", 0),
        "cache_read": rows.get("ai_tokens_cache_read", 0),
    }


def get_token_usage_month() -> dict:
    """Return current-month token usage counters as {input, output, cache_read}."""
    with _tx(_conn()) as conn:
        _maybe_rollover_month(conn)
        rows = _read_counters(conn, _TOKEN_MONTH_KEYS)
    return {
        "input": rows.get("ai_tokens_month_input", 0),
        "output": rows.get("ai_tokens_month_output", 0),
        "cache_read": rows.get("ai_tokens_month_cache_read", 0),
    }


def reset_token_usage() -> None:
    """Zero out both the lifetime and current-month counters."""
    with _tx(_conn()) as conn:
        conn.execute(
            "DELETE FROM user_preferences WHERE key IN (?,?,?,?,?,?)",
            _TOKEN_KEYS + _TOKEN_MONTH_KEYS,
        )


_BUDGET_KEY = "ai_tokens_month_budget"


def get_token_budget() -> int:
    """Monthly token budget (input + output). 0 = no budget set."""
    raw = get_pref(_BUDGET_KEY)
    try:
        return max(0, int(raw)) if raw else 0
    except (TypeError, ValueError):
        return 0


def set_token_budget(tokens: int) -> None:
    set_pref(_BUDGET_KEY, str(max(0, int(tokens))))


def token_budget_status() -> dict | None:
    """None when no budget is set; else {'used', 'budget', 'pct'}.

    used = month input + output. cache_read is excluded — it matches the
    'Total' line shown in the AI settings panel.
    """
    budget = get_token_budget()
    if not budget:
        return None
    usage = get_token_usage_month()
    used = usage["input"] + usage["output"]
    return {"used": used, "budget": budget, "pct": used / budget * 100}


# ── goals CRUD ────────────────────────────────────────────────────────────────


def save_goal(
    type: str,
    description: str,
    target: float | None = None,
    unit: str | None = None,
    exercise_template_id: str | None = None,
    exercise_name: str | None = None,
    muscle_group: str | None = None,
    start_value: float | None = None,
) -> None:
    with _tx(_conn()) as conn:
        conn.execute(
            """INSERT INTO user_goals
               (type, description, target, unit, exercise_template_id,
                exercise_name, muscle_group, start_value, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (type, description, target, unit, exercise_template_id, exercise_name, muscle_group, start_value),
        )
    _invalidate_render_cache()


def get_goals() -> list[dict]:
    with _tx(_conn()) as conn:
        rows = conn.execute("SELECT * FROM user_goals WHERE achieved_at IS NULL ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def get_all_goals() -> list[dict]:
    with _tx(_conn()) as conn:
        rows = conn.execute("SELECT * FROM user_goals ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def clear_goals() -> None:
    with _tx(_conn()) as conn:
        conn.execute("DELETE FROM user_goals")
    _invalidate_render_cache()


def mark_goal_achieved(goal_id: int) -> None:
    with _tx(_conn()) as conn:
        conn.execute("UPDATE user_goals SET achieved_at = datetime('now') WHERE id = ?", (goal_id,))


def get_uncelebrated_achievements() -> list[dict]:
    """Achieved goals not yet shown in a startup celebration (watermark pref)."""
    last = get_pref("goals_celebrated_at")
    sql = "SELECT * FROM user_goals WHERE achieved_at IS NOT NULL"
    params: tuple = ()
    if last:
        sql += " AND achieved_at > ?"
        params = (last,)
    with _tx(_conn()) as conn:
        rows = conn.execute(sql + " ORDER BY achieved_at", params).fetchall()
    return [dict(r) for r in rows]


def mark_achievements_celebrated() -> None:
    """Advance the celebration watermark to the newest achieved_at.

    The watermark is copied verbatim from the DB's own achieved_at value —
    SQLite's datetime('now') format ("YYYY-MM-DD HH:MM:SS") differs from
    Python's ISO "T" separator, and mixing them breaks the lexicographic
    comparison in get_uncelebrated_achievements.
    """
    with _tx(_conn()) as conn:
        row = conn.execute("SELECT MAX(achieved_at) AS m FROM user_goals").fetchone()
    if row and row["m"]:
        set_pref("goals_celebrated_at", row["m"])


def delete_goal(goal_id: int) -> None:
    with _tx(_conn()) as conn:
        conn.execute("DELETE FROM user_goals WHERE id = ?", (goal_id,))
    _invalidate_render_cache()


def update_goal_fields(
    goal_id: int,
    description: str | None = None,
    target: float | None = None,
    unit: str | None = None,
    start_value: float | None = None,
) -> None:
    with _tx(_conn()) as conn:
        if description is not None:
            conn.execute("UPDATE user_goals SET description = ? WHERE id = ?", (description, goal_id))
        if target is not None:
            conn.execute("UPDATE user_goals SET target = ? WHERE id = ?", (target, goal_id))
        if unit is not None:
            conn.execute("UPDATE user_goals SET unit = ? WHERE id = ?", (unit, goal_id))
        if start_value is not None:
            conn.execute("UPDATE user_goals SET start_value = ? WHERE id = ?", (start_value, goal_id))
    _invalidate_render_cache()


# ── goals check-in timing ─────────────────────────────────────────────────────


def should_ask_goals() -> bool:
    """True on first run or when more than N days have passed since last goals check-in."""
    last = get_pref("goals_last_asked")
    if not last:
        return True
    try:
        days = int(get_pref("goals_checkin_days") or 7)
        dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        return (datetime.now(UTC) - dt).days >= days
    except Exception:
        return True


def mark_goals_asked() -> None:
    set_pref("goals_last_asked", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))


# ── auto coaching report timing ───────────────────────────────────────────────


def should_auto_report() -> bool:
    """True when the 7-day auto coaching report is due (and not disabled)."""
    if get_pref("auto_report") == "0":
        return False
    last = get_pref("report_last_generated")
    if not last:
        return True
    try:
        dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        return (datetime.now(UTC) - dt).days >= 7
    except Exception:
        return True


def mark_report_generated() -> None:
    set_pref("report_last_generated", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))


# ── typed preference accessors ────────────────────────────────────────────────


def get_height_cm() -> float | None:
    """The athlete's height in cm, stored per-profile in preferences."""
    raw = get_pref("height_cm")
    try:
        return float(raw) if raw else None
    except (TypeError, ValueError):
        return None
