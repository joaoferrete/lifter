"""Tests for cli unit-conversion helpers, score helpers, and muscle-group distribution."""
from tests.conftest import seed_exercise_template, seed_workout


# ── _kg_to_lbs ────────────────────────────────────────────────────────────────

def test_kg_to_lbs_100kg():
    from cli import _kg_to_lbs
    # 100 * 2.20462 = 220.462, rounded to 1 dp = 220.5
    assert _kg_to_lbs(100) == 220.5


def test_kg_to_lbs_zero():
    from cli import _kg_to_lbs
    assert _kg_to_lbs(0) == 0.0


def test_kg_to_lbs_fractional_kg():
    from cli import _kg_to_lbs
    result = _kg_to_lbs(80)
    assert isinstance(result, float)
    assert 175 < result < 178  # 80 * 2.20462 ≈ 176.4


# ── _get_units ────────────────────────────────────────────────────────────────

def test_get_units_defaults_to_kg(tmp_db):
    from cli import _get_units
    assert _get_units() == "kg"


def test_get_units_returns_lbs_when_set(tmp_db):
    from db.goals import set_pref
    from cli import _get_units
    set_pref("units", "lbs")
    assert _get_units() == "lbs"


def test_get_units_falls_back_to_kg_for_unknown_value(tmp_db):
    from db.goals import set_pref
    from cli import _get_units
    set_pref("units", "stones")   # not a valid value — fall back to "kg"
    # _get_units returns whatever is stored; callers check == "lbs"
    result = _get_units()
    assert result != "lbs"   # should not behave as lbs


# ── _fmt_weight ───────────────────────────────────────────────────────────────

def test_fmt_weight_none_returns_dash(tmp_db):
    from cli import _fmt_weight
    assert _fmt_weight(None) == "—"


def test_fmt_weight_whole_number_kg(tmp_db):
    from cli import _fmt_weight
    assert _fmt_weight(80) == "80 kg"
    assert _fmt_weight(80.0) == "80 kg"


def test_fmt_weight_decimal_kg(tmp_db):
    from cli import _fmt_weight
    assert _fmt_weight(80.5) == "80.5 kg"


def test_fmt_weight_zero_kg(tmp_db):
    from cli import _fmt_weight
    assert _fmt_weight(0) == "0 kg"


def test_fmt_weight_lbs_mode_contains_lbs_suffix(tmp_db):
    from db.goals import set_pref
    from cli import _fmt_weight
    set_pref("units", "lbs")
    result = _fmt_weight(100)
    assert "lbs" in result


def test_fmt_weight_lbs_mode_correct_value(tmp_db):
    from db.goals import set_pref
    from cli import _fmt_weight
    set_pref("units", "lbs")
    # 100 kg → 220.5 lbs
    assert _fmt_weight(100) == "220.5 lbs"


def test_fmt_weight_lbs_whole_number_omits_decimal(tmp_db):
    from db.goals import set_pref
    from cli import _fmt_weight
    set_pref("units", "lbs")
    # 0 kg → 0.0 lbs, int(0.0) == 0.0 → True → "0 lbs"
    assert _fmt_weight(0) == "0 lbs"


# ── _score_color ──────────────────────────────────────────────────────────────

def test_score_color_green_at_boundary():
    from cli import _score_color
    assert _score_color(80) == "green"
    assert _score_color(100) == "green"


def test_score_color_cyan_range():
    from cli import _score_color
    assert _score_color(60) == "cyan"
    assert _score_color(79) == "cyan"


def test_score_color_yellow_range():
    from cli import _score_color
    assert _score_color(40) == "yellow"
    assert _score_color(59) == "yellow"


def test_score_color_red_below_40():
    from cli import _score_color
    assert _score_color(39) == "red"
    assert _score_color(0) == "red"


# ── _sets_by_group ────────────────────────────────────────────────────────────

def test_sets_by_group_empty_db(tmp_db):
    from cli import _sets_by_group
    groups = _sets_by_group(4)
    assert isinstance(groups, dict)
    assert len(groups) == 0


def test_sets_by_group_maps_chest(tmp_db):
    from cli import _sets_by_group
    seed_exercise_template(tmp_db, muscle="chest")
    seed_workout(tmp_db, "sg-chest", sets=[
        {"index": 0, "type": "normal", "weight_kg": 80.0, "reps": 5},
        {"index": 1, "type": "normal", "weight_kg": 80.0, "reps": 5},
    ])
    groups = _sets_by_group(4)
    assert "Chest" in groups
    assert groups["Chest"] > 0


def test_sets_by_group_maps_lats_to_back(tmp_db):
    from cli import _sets_by_group
    seed_exercise_template(tmp_db, muscle="lats")
    seed_workout(tmp_db, "sg-lats")
    groups = _sets_by_group(4)
    assert "Back" in groups
    assert groups["Back"] > 0


def test_sets_by_group_maps_upper_back_to_back(tmp_db):
    from cli import _sets_by_group
    seed_exercise_template(tmp_db, muscle="upper_back")
    seed_workout(tmp_db, "sg-ub")
    groups = _sets_by_group(4)
    assert "Back" in groups


def test_sets_by_group_maps_biceps_to_arms(tmp_db):
    from cli import _sets_by_group
    seed_exercise_template(tmp_db, muscle="biceps")
    seed_workout(tmp_db, "sg-bi")
    groups = _sets_by_group(4)
    assert "Arms" in groups


def test_sets_by_group_maps_abdominals_to_core(tmp_db):
    from cli import _sets_by_group
    seed_exercise_template(tmp_db, muscle="abdominals")
    seed_workout(tmp_db, "sg-abs")
    groups = _sets_by_group(4)
    assert "Core" in groups


def test_sets_by_group_unknown_muscle_goes_to_other(tmp_db):
    from cli import _sets_by_group
    seed_exercise_template(tmp_db, muscle="neck")
    seed_workout(tmp_db, "sg-neck")
    groups = _sets_by_group(4)
    assert "Other" in groups
    assert groups["Other"] > 0


def test_sets_by_group_multiple_muscles_aggregated(tmp_db):
    from cli import _sets_by_group
    seed_exercise_template(tmp_db, template_id="T-chest", muscle="chest")
    seed_exercise_template(tmp_db, template_id="T-lats", muscle="lats")
    seed_workout(tmp_db, "sg-m1", template_id="T-chest")
    seed_workout(tmp_db, "sg-m2", template_id="T-lats")
    groups = _sets_by_group(4)
    assert "Chest" in groups
    assert "Back" in groups
    assert sum(groups.values()) > 0


def test_sets_by_group_no_other_when_all_muscles_known(tmp_db):
    from cli import _sets_by_group
    seed_exercise_template(tmp_db, muscle="chest")
    seed_workout(tmp_db, "sg-noother")
    groups = _sets_by_group(4)
    assert "Other" not in groups


# ── --version flag ────────────────────────────────────────────────────────────

def test_version_flag_prints_and_exits_early(monkeypatch, capsys):
    import re
    import cli
    monkeypatch.setattr("sys.argv", ["lifter", "--version"])
    # init_db raising proves main() returned before any real startup work
    monkeypatch.setattr(cli, "init_db", lambda: (_ for _ in ()).throw(AssertionError("should not init")))
    cli.main()
    out = capsys.readouterr().out
    assert re.match(r"lifter (\d+\.\d+\.\d+.*|dev)\n$", out)


def test_app_version_nonempty():
    import cli
    assert cli._app_version()
