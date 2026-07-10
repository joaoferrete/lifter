"""Workout frequency, consistency, and session duration analytics."""

import pandas as pd

from analytics.common import weeks_denominator
from db.store import query


def workout_frequency(weeks: int | str = 8) -> dict:
    """Summary of training frequency and session duration.

    Coerces string input defensively — callers historically pass "8"-style values.
    """
    weeks = max(1, int(weeks))
    rows = query(
        """
        SELECT start_time, end_time
        FROM workouts
        WHERE start_time >= datetime('now', ?)
        ORDER BY start_time
        """,
        (f"-{weeks * 7} days",),
    )

    if not rows:
        return {
            "total_workouts": 0,
            "weeks_covered": weeks,
            "avg_per_week": 0.0,
            "avg_duration_minutes": 0.0,
            "longest_streak_days": 0,
            "rest_day_avg": 0.0,
        }

    df = pd.DataFrame(rows)
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True)
    df["end_time"] = pd.to_datetime(df["end_time"], utc=True)
    df["duration_min"] = (df["end_time"] - df["start_time"]).dt.total_seconds() / 60
    df["date"] = df["start_time"].dt.date

    unique_days = sorted(df["date"].unique())
    total = len(df)

    avg_duration = df["duration_min"].median()

    # Rest days between consecutive workout days
    if len(unique_days) > 1:
        gaps = [(unique_days[i + 1] - unique_days[i]).days for i in range(len(unique_days) - 1)]
        rest_avg = sum(gaps) / len(gaps)
    else:
        rest_avg = 0.0

    # Longest consecutive training streak (days)
    streak = max_streak = 1
    for i in range(1, len(unique_days)):
        if (unique_days[i] - unique_days[i - 1]).days == 1:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 1

    return {
        "total_workouts": total,
        "weeks_covered": weeks,
        "avg_per_week": round(total / weeks_denominator(weeks), 1),
        "avg_duration_minutes": round(avg_duration, 0),
        "longest_streak_days": max_streak,
        "rest_day_avg": round(rest_avg, 1),
    }


def weekly_workout_counts(weeks: int = 12) -> pd.DataFrame:
    rows = query(
        """
        SELECT start_time FROM workouts
        WHERE start_time >= datetime('now', ?)
        ORDER BY start_time
        """,
        (f"-{weeks * 7} days",),
    )

    if not rows:
        return pd.DataFrame(columns=["week", "count"])

    df = pd.DataFrame(rows)
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True)
    df["week"] = df["start_time"].dt.to_period("W").dt.start_time
    return df.groupby("week").size().reset_index(name="count").sort_values("week")


def muscle_group_frequency(weeks: int = 8) -> dict[str, float]:
    """Average sessions per week each muscle group is trained."""
    rows = query(
        """
        SELECT w.start_time, et.primary_muscle_group AS muscle
        FROM workout_exercises we
        JOIN workouts w ON w.id = we.workout_id
        JOIN exercise_templates et ON et.id = we.exercise_template_id
        WHERE w.start_time >= datetime('now', ?)
        GROUP BY w.id, et.primary_muscle_group
        """,
        (f"-{weeks * 7} days",),
    )

    if not rows:
        return {}

    df = pd.DataFrame(rows)
    counts = df.groupby("muscle").size() / weeks_denominator(weeks)
    return dict(counts.round(2).sort_values(ascending=False))
