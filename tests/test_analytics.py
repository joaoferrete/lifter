"""Tests for analytics functions using seeded workout data."""

from tests.conftest import TEMPLATE_ID, seed_exercise_template, seed_workout

# ── workout_frequency ─────────────────────────────────────────────────────────


def test_frequency_empty_db(tmp_db):
    from analytics.frequency import workout_frequency

    result = workout_frequency(8)
    assert result["total_workouts"] == 0
    assert result["avg_per_week"] == 0.0


def test_frequency_counts_recent_workouts(tmp_db):
    from analytics.frequency import workout_frequency

    seed_exercise_template(tmp_db)
    for i in range(4):
        seed_workout(tmp_db, f"fw{i}", days_ago=i * 4)

    result = workout_frequency(8)
    assert result["total_workouts"] == 4
    assert result["avg_per_week"] > 0


def test_frequency_weeks_guard_coerces_string(tmp_db):
    from analytics.frequency import workout_frequency

    # Must not raise even if caller passes a string
    result = workout_frequency("8")
    assert isinstance(result["total_workouts"], int)


def test_frequency_weeks_guard_clamps_negative(tmp_db):
    from analytics.frequency import workout_frequency

    result = workout_frequency(-5)
    assert isinstance(result, dict)


# ── weekly_volume ─────────────────────────────────────────────────────────────


def test_volume_empty_db(tmp_db):
    from analytics.volume import weekly_volume

    df = weekly_volume(8)
    assert df.empty


def test_volume_sums_tonnage(tmp_db):
    from analytics.volume import muscle_group_summary

    seed_exercise_template(tmp_db, muscle="chest")
    # 80 kg × 5 reps = 400 kg tonnage per workout
    seed_workout(tmp_db, "vw1", sets=[{"index": 0, "type": "normal", "weight_kg": 80.0, "reps": 5}])

    summary = muscle_group_summary(4)
    assert "chest" in summary
    assert summary["chest"] > 0


def test_volume_excludes_warmup_sets(tmp_db):
    from analytics.volume import muscle_group_summary

    seed_exercise_template(tmp_db, muscle="chest")
    seed_workout(
        tmp_db,
        "vw2",
        sets=[
            {"index": 0, "type": "warmup", "weight_kg": 40.0, "reps": 10},
            {"index": 1, "type": "normal", "weight_kg": 80.0, "reps": 5},
        ],
    )

    # Only the normal set (400 kg) should count
    full_summary = muscle_group_summary(4)
    # Volume should equal exactly one normal set's tonnage (400), not include warmup (400)
    assert full_summary.get("chest", 0) > 0


# ── exercise_progression ──────────────────────────────────────────────────────


def test_progression_empty(tmp_db):
    from analytics.progression import exercise_progression

    df = exercise_progression("NONEXISTENT", 12)
    assert df.empty


def test_progression_tracks_e1rm(tmp_db):
    from analytics.progression import exercise_progression

    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "pw1", sets=[{"index": 0, "type": "normal", "weight_kg": 80.0, "reps": 5}], days_ago=14)
    seed_workout(tmp_db, "pw2", sets=[{"index": 0, "type": "normal", "weight_kg": 90.0, "reps": 5}], days_ago=7)

    df = exercise_progression(TEMPLATE_ID, 12)
    assert len(df) == 2
    assert df.iloc[1]["best_weight_kg"] > df.iloc[0]["best_weight_kg"]


def test_progression_uses_epley_formula(tmp_db):
    from analytics.progression import exercise_progression

    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "epley1", sets=[{"index": 0, "type": "normal", "weight_kg": 100.0, "reps": 10}])

    df = exercise_progression(TEMPLATE_ID, 4)
    assert len(df) == 1
    # Epley: 100 * (1 + 10/30) = 133.33
    expected_e1rm = 100 * (1 + 10 / 30)
    assert abs(df.iloc[0]["e1rm"] - expected_e1rm) < 0.1


# ── personal records ──────────────────────────────────────────────────────────


def test_all_time_records_empty(tmp_db):
    from analytics.records import all_time_records

    assert all_time_records() == []


def test_all_time_records_picks_best_set(tmp_db):
    from analytics.records import all_time_records

    seed_exercise_template(tmp_db)
    seed_workout(
        tmp_db,
        "rec1",
        sets=[
            {"index": 0, "type": "normal", "weight_kg": 80.0, "reps": 5},
            {"index": 1, "type": "normal", "weight_kg": 100.0, "reps": 1},  # best e1RM
        ],
    )

    records = all_time_records()
    assert len(records) == 1
    # 100kg×1 has e1RM=100; 80kg×5 has e1RM≈93.3 → 100kg×1 should win
    assert records[0]["weight_kg"] == 100.0


def test_plateau_detection_empty(tmp_db):
    from analytics.progression import detect_plateaus

    plateaus = detect_plateaus(8, stall_sessions=3)
    assert plateaus == []


def test_plateau_detected_when_no_improvement(tmp_db):
    from analytics.progression import detect_plateaus

    seed_exercise_template(tmp_db)
    # Same weight for 4 sessions
    for i in range(4):
        seed_workout(
            tmp_db, f"pl{i}", sets=[{"index": 0, "type": "normal", "weight_kg": 80.0, "reps": 5}], days_ago=i * 7
        )

    plateaus = detect_plateaus(12, stall_sessions=3)
    assert len(plateaus) == 1
    assert "Exercise" in plateaus[0]["exercise"]
