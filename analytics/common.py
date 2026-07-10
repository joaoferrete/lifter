"""Shared helpers for the analytics modules."""

import pandas as pd

from db.store import query


def weeks_denominator(requested_weeks: int) -> float:
    """Effective week count for per-week averages over the last N weeks.

    Always the *requested* window — an athlete who trained in only 2 of the
    last 8 weeks averaged their volume over 8 weeks, not 2 (dividing by
    weeks-with-data inflates sparse training). The only clamp is training age:
    someone whose first workout was 3 weeks ago is averaged over 3 weeks, so
    new users aren't diluted by weeks that predate their history.
    """
    requested = max(1, int(requested_weeks))
    rows = query("SELECT (julianday('now') - julianday(MIN(start_time))) / 7.0 AS weeks_active FROM workouts")
    weeks_active = rows[0]["weeks_active"] if rows and rows[0]["weeks_active"] is not None else None
    if weeks_active is None:
        return float(requested)
    return float(max(1.0, min(float(requested), weeks_active)))


def df_with_time(rows: list[dict], *, week: bool = False, date: bool = False) -> pd.DataFrame:
    """DataFrame from query rows with `start_time` parsed (and week/date columns)."""
    df = pd.DataFrame(rows)
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True)
    if week:
        df["week"] = df["start_time"].dt.tz_convert(None).dt.to_period("W")
    if date:
        df["date"] = df["start_time"].dt.date
    return df
