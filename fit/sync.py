"""Sync sleep, steps, calories, and heart rate from Google Fit."""
import sqlite3
from datetime import datetime, timezone, timedelta

from config import DB_PATH
from fit.client import FitClient


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _date_of_ms(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def sync_fit(days: int = 30) -> dict:
    client = FitClient()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    counts = {"daily_days": 0, "sleep_sessions": 0}

    _sync_daily(client, start, end, counts)
    _sync_sleep(client, start, end, counts)

    from db.store import set_sync_state
    set_sync_state("fit_last_sync", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    return counts


def _sync_daily(client: FitClient, start: datetime, end: datetime, counts: dict) -> None:
    data = client.aggregate(
        data_types=[
            "com.google.step_count.delta",
            "com.google.calories.expended",
            "com.google.heart_rate.bpm",
            "com.google.active_minutes",
        ],
        start_ms=_ms(start),
        end_ms=_ms(end),
    )

    with _conn() as conn:
        for bucket in data.get("bucket", []):
            date = _date_of_ms(bucket["startTimeMillis"])
            row: dict = {
                "date": date,
                "steps": None,
                "total_calories": None,
                "avg_hr": None,
                "min_hr": None,
                "active_minutes": None,
            }

            for dataset in bucket.get("dataset", []):
                points = dataset.get("point", [])
                if not points:
                    continue
                dtype = points[0].get("dataTypeName", "")
                vals = points[0].get("value", [])

                if "step_count" in dtype and vals:
                    row["steps"] = vals[0].get("intVal") or int(vals[0].get("fpVal", 0) or 0)
                elif "calories.expended" in dtype and vals:
                    row["total_calories"] = vals[0].get("fpVal")
                elif "heart_rate" in dtype:
                    # aggregate returns [avg, max, min]
                    if len(vals) >= 1: row["avg_hr"] = vals[0].get("fpVal")
                    if len(vals) >= 3: row["min_hr"] = vals[2].get("fpVal")
                elif "active_minutes" in dtype and vals:
                    row["active_minutes"] = vals[0].get("intVal") or int(vals[0].get("fpVal", 0) or 0)

            conn.execute(
                """INSERT INTO fit_daily
                   (date, steps, total_calories, avg_hr, min_hr, active_minutes)
                   VALUES (:date, :steps, :total_calories, :avg_hr, :min_hr, :active_minutes)
                   ON CONFLICT(date) DO UPDATE SET
                     steps=excluded.steps,
                     total_calories=excluded.total_calories,
                     avg_hr=excluded.avg_hr,
                     min_hr=excluded.min_hr,
                     active_minutes=excluded.active_minutes""",
                row,
            )
            counts["daily_days"] += 1


def _sync_sleep(client: FitClient, start: datetime, end: datetime, counts: dict) -> None:
    sessions = client.get_sleep_sessions(_iso(start), _iso(end))

    with _conn() as conn:
        for s in sessions:
            start_ms = int(s["startTimeMillis"])
            end_ms = int(s["endTimeMillis"])
            date = _date_of_ms(start_ms)
            total_min = (end_ms - start_ms) // 60_000

            conn.execute(
                """INSERT INTO fit_sleep (date, total_minutes)
                   VALUES (?, ?)
                   ON CONFLICT(date) DO UPDATE SET total_minutes=excluded.total_minutes""",
                (date, total_min),
            )
            counts["sleep_sessions"] += 1
