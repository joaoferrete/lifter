"""Tests for Google Fit analytics — sleep, activity, recovery score, AI context."""
import sqlite3
from datetime import date, timedelta


def _today(offset=0):
    return (date.today() - timedelta(days=offset)).isoformat()


def _insert_sleep(db_path, day_offset=0, minutes=480):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO fit_sleep (date, total_minutes) VALUES (?, ?)",
        (_today(day_offset), minutes),
    )
    conn.commit()
    conn.close()


def _insert_daily(db_path, day_offset=0, steps=None, calories=None, avg_hr=None, min_hr=None, active_min=None):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO fit_daily (date, steps, total_calories, avg_hr, min_hr, active_minutes)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (_today(day_offset), steps, calories, avg_hr, min_hr, active_min),
    )
    conn.commit()
    conn.close()


# ── sleep_summary ─────────────────────────────────────────────────────────────

def test_sleep_summary_empty_db(tmp_db):
    from fit.analytics import sleep_summary
    assert sleep_summary() == {}


def test_sleep_summary_returns_avg_hours(tmp_db):
    from fit.analytics import sleep_summary
    _insert_sleep(tmp_db, day_offset=0, minutes=480)  # 8 h
    _insert_sleep(tmp_db, day_offset=1, minutes=420)  # 7 h
    result = sleep_summary(days=7)
    assert result["avg_hours"] == 7.5
    assert result["nights_tracked"] == 2


def test_sleep_summary_counts_nights_above_7h(tmp_db):
    from fit.analytics import sleep_summary
    _insert_sleep(tmp_db, day_offset=0, minutes=480)  # ≥ 7 h
    _insert_sleep(tmp_db, day_offset=1, minutes=300)  # < 7 h
    result = sleep_summary(days=7)
    assert result["nights_7plus_hours"] == 1
    assert result["consistency_pct"] == 50


def test_sleep_summary_all_nights_below_7h(tmp_db):
    from fit.analytics import sleep_summary
    _insert_sleep(tmp_db, day_offset=0, minutes=300)
    result = sleep_summary(days=7)
    assert result["nights_7plus_hours"] == 0
    assert result["consistency_pct"] == 0


def test_sleep_summary_last_night_hours(tmp_db):
    from fit.analytics import sleep_summary
    _insert_sleep(tmp_db, day_offset=0, minutes=540)  # 9 h most recent
    _insert_sleep(tmp_db, day_offset=1, minutes=420)
    result = sleep_summary(days=7)
    assert result["last_night_hours"] == 9.0


# ── activity_summary ──────────────────────────────────────────────────────────

def test_activity_summary_empty_db(tmp_db):
    from fit.analytics import activity_summary
    assert activity_summary() == {}


def test_activity_summary_averages_steps_and_rhr(tmp_db):
    from fit.analytics import activity_summary
    _insert_daily(tmp_db, day_offset=0, steps=8000, min_hr=55)
    _insert_daily(tmp_db, day_offset=1, steps=10000, min_hr=57)
    result = activity_summary(days=7)
    assert result["avg_steps"] == 9000.0
    assert result["resting_hr"] == 56.0


def test_activity_summary_skips_null_columns(tmp_db):
    from fit.analytics import activity_summary
    _insert_daily(tmp_db, day_offset=0, steps=None, min_hr=60)
    _insert_daily(tmp_db, day_offset=1, steps=5000, min_hr=None)
    result = activity_summary(days=7)
    assert result["avg_steps"] == 5000.0
    assert result["resting_hr"] == 60.0


def test_activity_summary_days_tracked(tmp_db):
    from fit.analytics import activity_summary
    _insert_daily(tmp_db, day_offset=0)
    _insert_daily(tmp_db, day_offset=1)
    result = activity_summary(days=7)
    assert result["days_tracked"] == 2


# ── recovery_score ────────────────────────────────────────────────────────────

def test_recovery_score_none_when_no_data(tmp_db):
    from fit.analytics import recovery_score
    assert recovery_score() is None


def test_recovery_score_none_when_only_sleep(tmp_db):
    from fit.analytics import recovery_score
    _insert_sleep(tmp_db, minutes=480)
    assert recovery_score() is None


def test_recovery_score_none_when_only_activity(tmp_db):
    from fit.analytics import recovery_score
    _insert_daily(tmp_db, min_hr=55)
    assert recovery_score() is None


def test_recovery_score_in_valid_range(tmp_db):
    from fit.analytics import recovery_score
    _insert_sleep(tmp_db, minutes=480)
    _insert_daily(tmp_db, min_hr=55)
    result = recovery_score()
    assert result is not None
    assert 0 <= result["score"] <= 100
    assert result["label"] in ("Excellent", "Good", "Fair", "Poor")
    assert result["color"] in ("green", "cyan", "yellow", "red")


def test_recovery_score_excellent_with_ideal_data(tmp_db):
    from fit.analytics import recovery_score
    _insert_sleep(tmp_db, minutes=480)  # 8 h → full sleep pts
    _insert_daily(tmp_db, min_hr=50)    # 50 bpm → max HR pts
    result = recovery_score()
    assert result["label"] == "Excellent"
    assert result["score"] >= 80


def test_recovery_score_poor_with_bad_data(tmp_db):
    from fit.analytics import recovery_score
    _insert_sleep(tmp_db, minutes=180)  # 3 h
    _insert_daily(tmp_db, min_hr=90)    # high resting HR
    result = recovery_score()
    assert result["label"] == "Poor"
    assert result["score"] < 45


def test_recovery_score_neutral_hr_pts_when_no_hr(tmp_db):
    from fit.analytics import recovery_score
    _insert_sleep(tmp_db, minutes=480)
    _insert_daily(tmp_db, min_hr=None, steps=8000)  # no HR data
    result = recovery_score()
    # HR pts defaults to 25 (neutral) — result should still be non-None
    assert result is not None
    assert result["resting_hr"] is None


# ── fit_context_for_ai ────────────────────────────────────────────────────────

def test_fit_context_no_data_returns_sentinel(tmp_db):
    from fit.analytics import fit_context_for_ai
    assert "No Google Fit" in fit_context_for_ai()


def test_fit_context_includes_sleep_data(tmp_db):
    from fit.analytics import fit_context_for_ai
    _insert_sleep(tmp_db, minutes=480)
    result = fit_context_for_ai(days=7)
    assert "sleep" in result.lower()


def test_fit_context_includes_resting_hr(tmp_db):
    from fit.analytics import fit_context_for_ai
    _insert_sleep(tmp_db, minutes=480)
    _insert_daily(tmp_db, min_hr=58)
    result = fit_context_for_ai(days=7)
    assert "58" in result


def test_fit_context_includes_recovery_score(tmp_db):
    from fit.analytics import fit_context_for_ai
    _insert_sleep(tmp_db, minutes=480)
    _insert_daily(tmp_db, min_hr=58)
    result = fit_context_for_ai(days=7)
    assert "Recovery score" in result or "recovery" in result.lower()
