"""Exercise progression tracking and plateau detection."""

import pandas as pd

from analytics.common import df_with_time
from analytics.e1rm import NORMAL_SET_FILTER_SQL, e1rm
from db.store import query


def exercise_progression(template_id: str, weeks: int = 12) -> pd.DataFrame:
    """Best estimated 1RM per session for a given exercise template."""
    weeks = max(1, int(weeks))
    rows = query(
        f"""
        SELECT w.start_time, ws.weight_kg, ws.reps
        FROM workout_sets ws
        JOIN workouts w ON w.id = ws.workout_id
        WHERE ws.exercise_template_id = ?
          AND {NORMAL_SET_FILTER_SQL}
          AND w.start_time >= datetime('now', ?)
        ORDER BY w.start_time
        """,
        (template_id, f"-{weeks * 7} days"),
    )

    if not rows:
        return pd.DataFrame(columns=["date", "best_weight_kg", "best_reps", "e1rm"])

    df = df_with_time(rows, date=True)
    df["e1rm"] = df.apply(lambda r: e1rm(r["weight_kg"], r["reps"]), axis=1)

    best = (
        df.sort_values("e1rm", ascending=False)
        .groupby("date")
        .first()
        .reset_index()[["date", "weight_kg", "reps", "e1rm"]]
        .rename(columns={"weight_kg": "best_weight_kg", "reps": "best_reps"})
        .sort_values("date")
    )
    return best


def all_exercise_progressions(weeks: int = 12) -> dict[str, pd.DataFrame]:
    """Return progression data for every exercise that has at least 3 sessions.

    Uses a single query + in-pandas grouping instead of one query per exercise
    (was an N+1 hot path called twice per coaching report)."""
    weeks = max(1, int(weeks))
    rows = query(
        f"""
        SELECT ws.exercise_template_id AS template_id, et.title AS title,
               w.start_time, ws.weight_kg, ws.reps
        FROM workout_sets ws
        JOIN workouts w ON w.id = ws.workout_id
        JOIN exercise_templates et ON et.id = ws.exercise_template_id
        WHERE {NORMAL_SET_FILTER_SQL}
          AND w.start_time >= datetime('now', ?)
        ORDER BY w.start_time
        """,
        (f"-{weeks * 7} days",),
    )
    if not rows:
        return {}

    df = df_with_time(rows, date=True)
    df["e1rm"] = df.apply(lambda r: e1rm(r["weight_kg"], r["reps"]), axis=1)

    # Best e1RM per exercise per day (mirrors exercise_progression's per-day pick).
    best = df.sort_values("e1rm", ascending=False).groupby(["template_id", "date"], as_index=False).first()

    result: dict[str, pd.DataFrame] = {}
    for _template_id, g in best.groupby("template_id"):
        per = (
            g.sort_values("date")
            .reset_index(drop=True)[["date", "weight_kg", "reps", "e1rm", "title"]]
            .rename(columns={"weight_kg": "best_weight_kg", "reps": "best_reps"})
        )
        if len(per) >= 3:
            title = per["title"].iloc[0]
            result[title] = per.drop(columns=["title"])
    return result


def detect_plateaus(weeks: int = 8, stall_sessions: int = 3) -> list[dict]:
    """Find exercises where the e1RM hasn't improved in the last N sessions."""
    progressions = all_exercise_progressions(weeks)
    plateaus = []
    for title, df in progressions.items():
        if len(df) < stall_sessions:
            continue
        tail = df.tail(stall_sessions)
        if tail["e1rm"].max() <= tail["e1rm"].iloc[0]:
            plateaus.append(
                {
                    "exercise": title,
                    "sessions_stalled": stall_sessions,
                    "current_e1rm": round(tail["e1rm"].iloc[-1], 1),
                    "last_date": str(tail["date"].iloc[-1]),
                }
            )
    return plateaus


def top_progressions(weeks: int = 8, top_n: int = 5) -> list[dict]:
    """Exercises with the best relative e1RM improvement over the period."""
    progressions = all_exercise_progressions(weeks)
    improvements = []
    for title, df in progressions.items():
        if len(df) < 2:
            continue
        start = df["e1rm"].iloc[0]
        end = df["e1rm"].iloc[-1]
        if start > 0:
            pct = (end - start) / start * 100
            improvements.append(
                {
                    "exercise": title,
                    "improvement_pct": round(pct, 1),
                    "start_e1rm": round(start, 1),
                    "current_e1rm": round(end, 1),
                }
            )
    return sorted(improvements, key=lambda x: x["improvement_pct"], reverse=True)[:top_n]
