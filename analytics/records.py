"""Personal records detection."""

import pandas as pd

from analytics.common import df_with_time
from analytics.e1rm import NORMAL_SET_FILTER_SQL, e1rm, e1rm_sql
from db.store import query


def current_e1rm_kg(template_id: str) -> float:
    """Best estimated 1RM (kg, 1 decimal) across all normal sets of an exercise; 0 when none."""
    rows = query(
        f"""SELECT MAX({e1rm_sql()}) as e1rm
           FROM workout_sets ws WHERE ws.exercise_template_id = ?
           AND {NORMAL_SET_FILTER_SQL}""",
        (template_id,),
    )
    return round(rows[0]["e1rm"], 1) if rows and rows[0]["e1rm"] else 0


def workout_best_sets(workout_id: str) -> list[dict]:
    """All normal sets of a workout with their e1RM, ordered by exercise position then e1RM desc."""
    return query(
        f"""SELECT we.title, we.exercise_template_id,
                  ws.weight_kg, ws.reps,
                  {e1rm_sql()} as e1rm
           FROM workout_exercises we
           JOIN workout_sets ws ON ws.workout_exercise_id = we.id
           WHERE we.workout_id = ? AND {NORMAL_SET_FILTER_SQL}
           ORDER BY we.idx, e1rm DESC""",
        (workout_id,),
    )


def max_e1rm_excluding_workout(template_id: str, workout_id: str) -> float | None:
    """Best e1RM for an exercise across all workouts except `workout_id` (PR check)."""
    rows = query(
        f"""SELECT MAX({e1rm_sql()}) as top
           FROM workout_sets ws WHERE ws.exercise_template_id = ?
           AND {NORMAL_SET_FILTER_SQL} AND ws.workout_id != ?""",
        (template_id, workout_id),
    )
    return rows[0]["top"] if rows else None


def all_time_records() -> list[dict]:
    """Best set (highest e1RM) ever recorded per exercise."""
    rows = query(
        f"""
        SELECT
            et.title AS exercise,
            ws.exercise_template_id,
            ws.weight_kg,
            ws.reps,
            w.start_time
        FROM workout_sets ws
        JOIN workouts w ON w.id = ws.workout_id
        JOIN exercise_templates et ON et.id = ws.exercise_template_id
        WHERE {NORMAL_SET_FILTER_SQL}
        """
    )

    if not rows:
        return []

    df = pd.DataFrame(rows)
    df["e1rm"] = df.apply(lambda r: e1rm(r["weight_kg"], r["reps"]), axis=1)
    best = df.sort_values("e1rm", ascending=False).groupby("exercise").first().reset_index()

    return [
        {
            "exercise": row["exercise"],
            "weight_kg": row["weight_kg"],
            "reps": int(row["reps"]),
            "e1rm": round(row["e1rm"], 1),
            "date": row["start_time"][:10],
        }
        for _, row in best.sort_values("e1rm", ascending=False).iterrows()
    ]


def recent_prs(days: int = 30) -> list[dict]:
    """PRs set in the last N days (e1RM higher than any prior session for that exercise)."""
    rows = query(
        f"""
        SELECT
            et.title AS exercise,
            ws.exercise_template_id,
            ws.weight_kg,
            ws.reps,
            w.start_time
        FROM workout_sets ws
        JOIN workouts w ON w.id = ws.workout_id
        JOIN exercise_templates et ON et.id = ws.exercise_template_id
        WHERE {NORMAL_SET_FILTER_SQL}
        ORDER BY w.start_time
        """
    )

    if not rows:
        return []

    df = df_with_time(rows)
    df["e1rm"] = df.apply(lambda r: e1rm(r["weight_kg"], r["reps"]), axis=1)

    # Track running max per exercise and find where a new max was set
    df = df.sort_values("start_time")
    df["prev_max"] = df.groupby("exercise_template_id")["e1rm"].transform(lambda x: x.shift(1).expanding().max())

    recent_cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    prs = df[(df["start_time"] >= recent_cutoff) & (df["e1rm"] > df["prev_max"].fillna(0))]

    return [
        {
            "exercise": row["exercise"],
            "weight_kg": row["weight_kg"],
            "reps": int(row["reps"]),
            "e1rm": round(row["e1rm"], 1),
            "date": row["start_time"].strftime("%Y-%m-%d"),
        }
        for _, row in prs.sort_values("start_time", ascending=False).drop_duplicates("exercise").iterrows()
    ]


def body_measurement_trend(weeks: int = 12) -> dict:
    """Latest body measurements and trend vs N weeks ago."""
    weeks = max(1, int(weeks))
    rows = query(
        """
        SELECT date, weight_kg, fat_percent, lean_mass_kg
        FROM body_measurements
        WHERE weight_kg IS NOT NULL
        ORDER BY date DESC
        LIMIT 1
        """
    )
    old_rows = query(
        """
        SELECT date, weight_kg, fat_percent, lean_mass_kg
        FROM body_measurements
        WHERE weight_kg IS NOT NULL
          AND date <= date('now', ?)
        ORDER BY date DESC
        LIMIT 1
        """,
        (f"-{weeks * 7} days",),
    )

    if not rows:
        return {}

    latest = rows[0]
    result = {
        "latest_date": latest["date"],
        "weight_kg": latest["weight_kg"],
        "fat_percent": latest["fat_percent"],
        "lean_mass_kg": latest["lean_mass_kg"],
    }

    if old_rows:
        old = old_rows[0]
        if old["weight_kg"] and latest["weight_kg"]:
            result["weight_change_kg"] = round(latest["weight_kg"] - old["weight_kg"], 1)
        if old["fat_percent"] and latest["fat_percent"]:
            result["fat_change_pct"] = round(latest["fat_percent"] - old["fat_percent"], 1)

    return result


# ── BMI ─────────────────────────────────────────────────────────────────────


def compute_bmi(weight_kg: float | str | None, height_cm: float | str | None) -> float | None:
    """Body Mass Index (kg/m²) rounded to 1 decimal, or None if inputs missing."""
    if weight_kg is None or height_cm is None:
        return None
    try:
        w = float(weight_kg)
        h = float(height_cm)
    except ValueError:
        return None
    if w <= 0 or h <= 0:
        return None
    return round(w / (h / 100) ** 2, 1)


def bmi_category(bmi: float | str | None) -> str | None:
    """WHO BMI category key (underweight/normal/overweight/obese)."""
    if bmi is None:
        return None
    try:
        b = float(bmi)
    except ValueError:
        return None
    if b < 18.5:
        return "underweight"
    if b < 25:
        return "normal"
    if b < 30:
        return "overweight"
    return "obese"
