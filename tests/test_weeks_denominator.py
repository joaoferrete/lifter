"""Per-week averages must divide by the requested window (clamped to training
age), not by weeks-with-data — sparse training used to be sharply inflated."""

from tests.conftest import seed_exercise_template, seed_workout


def test_denominator_is_requested_window_for_established_athlete(tmp_db):
    from analytics.common import weeks_denominator

    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "old", days_ago=70)  # training for 10 weeks
    assert weeks_denominator(8) == 8.0


def test_denominator_clamped_to_training_age_for_new_athlete(tmp_db):
    from analytics.common import weeks_denominator

    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "first", days_ago=14)  # training for ~2 weeks
    denom = weeks_denominator(8)
    assert 1.9 <= denom <= 2.1


def test_denominator_defaults_to_requested_with_no_workouts(tmp_db):
    from analytics.common import weeks_denominator

    assert weeks_denominator(8) == 8.0


def test_sparse_training_not_inflated_in_sets_per_week(tmp_db):
    """8 sets across an 8-week window with only 2 active weeks → 1 set/week, not 4."""
    from analytics.volume import sets_per_muscle_per_week

    seed_exercise_template(tmp_db, muscle="chest")
    four_sets = [{"index": i, "type": "normal", "weight_kg": 80.0, "reps": 5} for i in range(4)]
    # Two workouts: one 7.5 weeks ago (establishes training age ≥ window), one now.
    seed_workout(tmp_db, "w-old", days_ago=53, sets=four_sets)
    seed_workout(tmp_db, "w-new", days_ago=0, sets=four_sets)

    per_week = sets_per_muscle_per_week(8)
    assert per_week["chest"] == 1.1  # 8 sets / ~7.57 weeks of training age


def test_sparse_training_not_inflated_in_muscle_frequency(tmp_db):
    from analytics.frequency import muscle_group_frequency

    seed_exercise_template(tmp_db, muscle="chest")
    seed_workout(tmp_db, "w-old", days_ago=53)
    seed_workout(tmp_db, "w-new", days_ago=0)

    freq = muscle_group_frequency(8)
    # 2 sessions over ~7.57 weeks → ~0.26/wk; the old bug divided by 2 weeks-with-data (1.0/wk).
    assert freq["chest"] < 0.5


def test_muscle_group_summary_divides_by_window(tmp_db):
    from analytics.volume import muscle_group_summary

    seed_exercise_template(tmp_db, muscle="chest")
    # 80 kg × 5 reps × 2 sets = 800 kg tonnage in a single week of an 8-week window.
    seed_workout(tmp_db, "w-old", days_ago=53, sets=[{"index": 0, "type": "normal", "weight_kg": 80.0, "reps": 5}])
    seed_workout(tmp_db, "w-new", days_ago=0, sets=[{"index": 0, "type": "normal", "weight_kg": 80.0, "reps": 5}])

    summary = muscle_group_summary(8)
    # total 800 kg / ~7.57 weeks ≈ 105.7 — the old per-active-week mean said 400.
    assert summary["chest"] < 150


def test_avg_per_week_clamped_for_new_athlete(tmp_db):
    from analytics.frequency import workout_frequency

    seed_exercise_template(tmp_db)
    seed_workout(tmp_db, "w1", days_ago=0)
    seed_workout(tmp_db, "w2", days_ago=3)
    seed_workout(tmp_db, "w3", days_ago=7)  # training for ~1 week

    freq = workout_frequency(8)
    # 3 workouts in the athlete's single week of history → ~3/wk, not 3/8.
    assert freq["avg_per_week"] >= 2.5
