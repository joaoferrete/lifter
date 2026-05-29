"""User goals and preferences management."""
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ── preferences ───────────────────────────────────────────────────────────────

def get_pref(key: str) -> str | None:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM user_preferences WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_pref(key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO user_preferences (key, value, updated_at) VALUES (?, ?, datetime('now'))"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value),
        )


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
    with _conn() as conn:
        conn.execute(
            """INSERT INTO user_goals
               (type, description, target, unit, exercise_template_id,
                exercise_name, muscle_group, start_value, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (type, description, target, unit, exercise_template_id,
             exercise_name, muscle_group, start_value),
        )


def get_goals() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM user_goals WHERE achieved_at IS NULL ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_goals() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM user_goals ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def clear_goals() -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM user_goals")


def mark_goal_achieved(goal_id: int) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE user_goals SET achieved_at = datetime('now') WHERE id = ?", (goal_id,)
        )


# ── goals check-in timing ─────────────────────────────────────────────────────

def should_ask_goals() -> bool:
    """True on first run or when more than 7 days have passed since last goals check-in."""
    last = get_pref("goals_last_asked")
    if not last:
        return True
    try:
        dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days >= 7
    except Exception:
        return True


def mark_goals_asked() -> None:
    set_pref("goals_last_asked", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


# ── progress computation ──────────────────────────────────────────────────────

def compute_goal_progress() -> list[dict]:
    """Compute current progress for every active goal. Marks achieved goals."""
    from db.store import query

    goals = get_goals()
    if not goals:
        return []

    results = []
    newly_achieved: list[int] = []

    for goal in goals:
        result: dict = {
            "id": goal["id"],
            "type": goal["type"],
            "description": goal["description"],
            "target": goal["target"],
            "unit": goal["unit"] or "",
            "current": None,
            "pct": 0.0,
            "achieved": False,
            "exercise_name": goal.get("exercise_name"),
        }

        try:
            if goal["type"] == "lift_pr":
                rows = query(
                    """SELECT MAX(ws.weight_kg * (1 + ws.reps / 30.0)) as e1rm
                       FROM workout_sets ws
                       WHERE ws.exercise_template_id = ?
                         AND ws.type = 'normal'
                         AND ws.weight_kg IS NOT NULL AND ws.reps IS NOT NULL""",
                    (goal["exercise_template_id"],),
                )
                current = float(rows[0]["e1rm"] or 0) if rows else 0.0
                target = goal["target"] or 1.0
                result["current"] = round(current, 1)
                result["pct"] = min(current / target * 100, 100)
                result["achieved"] = current >= target

            elif goal["type"] == "frequency":
                from analytics.frequency import workout_frequency
                current = float(workout_frequency(4)["avg_per_week"])
                target = goal["target"] or 1.0
                result["current"] = round(current, 1)
                result["pct"] = min(current / target * 100, 100)
                result["achieved"] = current >= target

            elif goal["type"] in ("weight_loss", "weight_gain"):
                rows = query(
                    "SELECT weight_kg FROM body_measurements WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT 1"
                )
                if rows:
                    current = float(rows[0]["weight_kg"])
                    target = goal["target"] or current
                    start = goal["start_value"] or current
                    result["current"] = current
                    if goal["type"] == "weight_loss" and start > target:
                        result["pct"] = min((start - current) / (start - target) * 100, 100)
                        result["achieved"] = current <= target
                    elif goal["type"] == "weight_gain" and start < target:
                        result["pct"] = min((current - start) / (target - start) * 100, 100)
                        result["achieved"] = current >= target

            elif goal["type"] == "body_fat":
                rows = query(
                    "SELECT fat_percent FROM body_measurements WHERE fat_percent IS NOT NULL ORDER BY date DESC LIMIT 1"
                )
                if rows:
                    current = float(rows[0]["fat_percent"])
                    target = goal["target"] or current
                    start = goal["start_value"] or current
                    result["current"] = current
                    if start > target:
                        result["pct"] = min((start - current) / (start - target) * 100, 100)
                        result["achieved"] = current <= target

            elif goal["type"] == "volume":
                from analytics.volume import sets_per_muscle_per_week
                current = float(sets_per_muscle_per_week(4).get(goal["muscle_group"] or "", 0))
                target = goal["target"] or 1.0
                result["current"] = round(current, 1)
                result["pct"] = min(current / target * 100, 100)
                result["achieved"] = current >= target

            elif goal["type"] == "custom":
                result["pct"] = None  # no numeric progress; shown as text only

        except Exception:
            pass

        if result["achieved"]:
            newly_achieved.append(goal["id"])

        results.append(result)

    for gid in newly_achieved:
        mark_goal_achieved(gid)

    return results


def goals_context_for_ai(weeks: int = 8) -> str:
    """Return a text summary of goals + current progress for the AI system prompt."""
    goals = get_goals()
    if not goals:
        return "No goals set."

    progress = compute_goal_progress()
    prog_by_id = {p["id"]: p for p in progress}

    lines = ["## User goals"]
    for g in goals:
        p = prog_by_id.get(g["id"], {})
        current = p.get("current")
        pct = p.get("pct")
        pct_str = f" ({pct:.0f}%)" if pct is not None else ""
        current_str = f" — current: {current} {g.get('unit') or ''}" if current is not None else ""
        lines.append(f"  - {g['description']}{current_str}{pct_str}")

    return "\n".join(lines)
