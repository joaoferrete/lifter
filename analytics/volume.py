"""Weekly training volume (tonnage) per muscle group."""
import pandas as pd

from db.store import query


def weekly_volume(weeks: int = 8) -> pd.DataFrame:
    """Return weekly tonnage (kg × reps) per primary muscle group for the last N weeks."""
    rows = query(
        """
        SELECT
            w.start_time,
            et.primary_muscle_group AS muscle,
            ws.weight_kg,
            ws.reps,
            ws.type
        FROM workout_sets ws
        JOIN workouts w ON w.id = ws.workout_id
        JOIN exercise_templates et ON et.id = ws.exercise_template_id
        WHERE ws.type != 'warmup'
          AND ws.weight_kg IS NOT NULL
          AND ws.reps IS NOT NULL
          AND w.start_time >= datetime('now', ?)
        """,
        (f"-{weeks * 7} days",),
    )

    if not rows:
        return pd.DataFrame(columns=["week", "muscle", "volume_kg"])

    df = pd.DataFrame(rows)
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True)
    df["week"] = df["start_time"].dt.to_period("W").dt.start_time
    df["tonnage"] = df["weight_kg"] * df["reps"]

    return (
        df.groupby(["week", "muscle"])["tonnage"]
        .sum()
        .reset_index()
        .rename(columns={"tonnage": "volume_kg"})
        .sort_values(["week", "volume_kg"], ascending=[True, False])
    )


def muscle_group_summary(weeks: int = 8) -> dict[str, float]:
    """Return average weekly volume per muscle group as a simple dict."""
    df = weekly_volume(weeks)
    if df.empty:
        return {}
    avg = df.groupby("muscle")["volume_kg"].mean().round(1)
    return dict(avg.sort_values(ascending=False))


def sets_per_muscle_per_week(weeks: int = 8) -> dict[str, float]:
    """Average sets per week per muscle group — useful for volume landmark checks."""
    rows = query(
        """
        SELECT
            w.start_time,
            et.primary_muscle_group AS muscle
        FROM workout_sets ws
        JOIN workouts w ON w.id = ws.workout_id
        JOIN exercise_templates et ON et.id = ws.exercise_template_id
        WHERE ws.type != 'warmup'
          AND w.start_time >= datetime('now', ?)
        """,
        (f"-{weeks * 7} days",),
    )

    if not rows:
        return {}

    df = pd.DataFrame(rows)
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True)
    df["week"] = df["start_time"].dt.to_period("W")

    total_weeks = df["week"].nunique() or 1
    counts = df.groupby("muscle").size() / total_weeks
    return dict(counts.round(1).sort_values(ascending=False))
