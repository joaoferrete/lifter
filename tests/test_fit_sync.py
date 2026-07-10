"""Google Fit sync — sleep session aggregation and day attribution."""

from datetime import UTC, datetime
from unittest.mock import MagicMock


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp() * 1000)


def _session(start_iso: str, end_iso: str) -> dict:
    return {"startTimeMillis": str(_ms(start_iso)), "endTimeMillis": str(_ms(end_iso))}


def _run_sleep_sync(tmp_db, sessions):
    import fit.sync as fit_sync_mod

    client = MagicMock()
    client.get_sleep_sessions.return_value = sessions
    counts = {"sleep_sessions": 0}
    fit_sync_mod._sync_sleep(client, datetime.now(UTC), datetime.now(UTC), counts)
    return counts


def _sleep_rows(tmp_db):
    from db.store import query

    return {r["date"]: r["total_minutes"] for r in query("SELECT date, total_minutes FROM fit_sleep", db_path=tmp_db)}


def test_same_day_sessions_are_summed(tmp_db):
    """Night sleep + nap on the same day must add up, not overwrite each other."""
    counts = _run_sleep_sync(
        tmp_db,
        [
            _session("2024-03-10T23:30:00", "2024-03-11T07:00:00"),  # 450 min night
            _session("2024-03-11T14:00:00", "2024-03-11T15:00:00"),  # 60 min nap
        ],
    )
    rows = _sleep_rows(tmp_db)
    assert rows["2024-03-11"] == 450 + 60
    assert counts["sleep_sessions"] == 2


def test_session_attributed_to_wake_day(tmp_db):
    """A 23:30–07:00 session counts on the morning's date, not the previous day."""
    _run_sleep_sync(tmp_db, [_session("2024-03-10T23:30:00", "2024-03-11T07:00:00")])
    rows = _sleep_rows(tmp_db)
    assert "2024-03-11" in rows
    assert "2024-03-10" not in rows


def test_resync_is_idempotent(tmp_db):
    """Running the same sync twice must not double the totals."""
    sessions = [
        _session("2024-03-10T23:30:00", "2024-03-11T07:00:00"),
        _session("2024-03-11T14:00:00", "2024-03-11T15:00:00"),
    ]
    _run_sleep_sync(tmp_db, sessions)
    _run_sleep_sync(tmp_db, sessions)
    assert _sleep_rows(tmp_db)["2024-03-11"] == 510
