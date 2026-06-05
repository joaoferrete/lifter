"""Sync sleep, steps, calories, and heart rate from Google Fit."""
import os
import sqlite3
from datetime import datetime, timezone, timedelta

import config
from fit.client import FitClient


def _conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _date_of_ms(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _local_tz_id() -> str | None:
    """Return a valid IANA timezone ID for the local machine (e.g. 'America/Sao_Paulo').

    str(tzinfo) returns offsets like '-03:00' which the Google Fit API rejects.
    We need the IANA name (a slash-separated string like 'America/Sao_Paulo').
    """
    # Most reliable: /etc/timezone on Linux/Debian/Ubuntu
    try:
        from pathlib import Path
        tz = Path("/etc/timezone").read_text().strip()
        if tz and "/" in tz:
            return tz
    except Exception:
        pass

    # /etc/localtime is a symlink to the zoneinfo file on most Linux/macOS
    try:
        import os
        from pathlib import Path
        link = Path("/etc/localtime").resolve()
        for marker in ("zoneinfo/", "zoneinfo\\"):
            idx = str(link).find(marker)
            if idx != -1:
                candidate = str(link)[idx + len(marker):]
                if "/" in candidate:
                    return candidate
    except Exception:
        pass

    # Python 3.9+ zoneinfo: ZoneInfo objects have a .key attribute
    try:
        tz_info = datetime.now().astimezone().tzinfo
        if hasattr(tz_info, "key") and "/" in tz_info.key:
            return tz_info.key
    except Exception:
        pass

    # TZ env variable (sometimes set explicitly)
    tz = os.environ.get("TZ", "")
    if tz and "/" in tz:
        return tz

    return None  # fall back to UTC bucket alignment


def _sum_points(points: list[dict], field: str = "intVal") -> int | float | None:
    """Sum a numeric field across ALL points in a dataset.

    Google Fit can return multiple points within a bucket when multiple
    data sources contribute data (e.g. watch + phone + Samsung Health).
    Reading only points[0] misses the rest — we must sum them all.
    """
    total = 0
    found = False
    for point in points:
        vals = point.get("value", [])
        if not vals:
            continue
        v = vals[0].get(field)
        if v is not None:
            total += v
            found = True
    return total if found else None


def sync_fit(days: int = 30) -> dict:
    client = FitClient()

    # Use local midnight boundaries so day buckets align to the user's clock,
    # not UTC midnight. This prevents steps from being assigned to the wrong day.
    local_now = datetime.now()
    end = local_now.replace(hour=23, minute=59, second=59, microsecond=0)
    start = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)

    # Convert to UTC for the API call
    end_utc = end.astimezone(timezone.utc)
    start_utc = start.astimezone(timezone.utc)

    tz_id = _local_tz_id()
    counts = {"daily_days": 0, "sleep_sessions": 0}

    _sync_daily(client, start_utc, end_utc, tz_id, counts)
    _sync_sleep(client, start_utc, end_utc, counts)

    from db.store import set_sync_state
    set_sync_state("fit_last_sync", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    return counts


_AGGREGATE_CHUNK_DAYS = 14  # Google Fit period-bucket API rejects ranges > 14 days


def _sync_daily(
    client: FitClient,
    start: datetime,
    end: datetime,
    tz_id: str | None,
    counts: dict,
) -> None:
    data_types = [
        "com.google.step_count.delta",
        "com.google.calories.expended",
        "com.google.heart_rate.bpm",
        "com.google.active_minutes",
    ]

    all_buckets: list[dict] = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=_AGGREGATE_CHUNK_DAYS), end)
        chunk_data = client.aggregate(
            data_types=data_types,
            start_ms=_ms(chunk_start),
            end_ms=_ms(chunk_end),
            timezone_id=tz_id,
        )
        all_buckets.extend(chunk_data.get("bucket", []))
        chunk_start = chunk_end

    with _conn() as conn:
        for bucket in all_buckets:
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

                # Detect data type from the point (more reliable than dataSourceId substring)
                dtype = points[0].get("dataTypeName", "")
                # Fallback: derive type from the dataSourceId
                if not dtype:
                    src = dataset.get("dataSourceId", "")
                    if "step_count" in src:
                        dtype = "com.google.step_count.delta"
                    elif "calories" in src:
                        dtype = "com.google.calories.expended"
                    elif "heart_rate" in src:
                        dtype = "com.google.heart_rate.bpm"
                    elif "active_minutes" in src:
                        dtype = "com.google.active_minutes"

                if "step_count" in dtype:
                    # SUM across all points — multiple sources (phone, watch,
                    # Samsung Health) each contribute a separate point.
                    steps = _sum_points(points, "intVal")
                    if steps is None:
                        steps = _sum_points(points, "fpVal")
                    row["steps"] = int(steps) if steps is not None else None

                elif "calories.expended" in dtype:
                    cal = _sum_points(points, "fpVal")
                    if cal is not None:
                        row["total_calories"] = round(cal, 1)

                elif "heart_rate" in dtype:
                    # Aggregate HR returns value[0]=avg, value[1]=max, value[2]=min
                    # per bucket (single point).
                    vals = points[0].get("value", [])
                    if len(vals) >= 1 and vals[0].get("fpVal") is not None:
                        row["avg_hr"] = round(vals[0]["fpVal"], 1)
                    if len(vals) >= 3 and vals[2].get("fpVal") is not None:
                        row["min_hr"] = round(vals[2]["fpVal"], 1)

                elif "active_minutes" in dtype:
                    mins = _sum_points(points, "intVal")
                    if mins is None:
                        mins = _sum_points(points, "fpVal")
                    row["active_minutes"] = int(mins) if mins is not None else None

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


def _sync_sleep(
    client: FitClient,
    start: datetime,
    end: datetime,
    counts: dict,
) -> None:
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
