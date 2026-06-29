"""BMI / height helpers and manual body-measurement entry."""


# ── BMI & category (pure helpers) ─────────────────────────────────────────────

def test_compute_bmi_basic():
    from analytics.records import compute_bmi
    assert compute_bmi(80, 178) == 25.2
    assert compute_bmi(70, 175) == 22.9


def test_compute_bmi_invalid_inputs():
    from analytics.records import compute_bmi
    assert compute_bmi(None, 178) is None
    assert compute_bmi(80, None) is None
    assert compute_bmi(80, 0) is None
    assert compute_bmi("x", 178) is None


def test_bmi_category_boundaries():
    from analytics.records import bmi_category
    assert bmi_category(17) == "underweight"
    assert bmi_category(22) == "normal"
    assert bmi_category(27) == "overweight"
    assert bmi_category(31) == "obese"


# ── height storage & unit-aware parsing/formatting ────────────────────────────

def test_get_height_cm_roundtrip(tmp_db):
    from db.goals import set_pref
    from analytics.records import get_height_cm
    assert get_height_cm() is None
    set_pref("height_cm", "178")
    assert get_height_cm() == 178.0


def test_parse_height_metric(tmp_db):
    import cli
    from db.goals import set_pref
    set_pref("units", "kg")
    assert cli._parse_height_to_cm("178") == 178.0
    assert cli._parse_height_to_cm("") is None
    assert cli._parse_height_to_cm("abc") is None


def test_parse_height_imperial(tmp_db):
    import cli
    from db.goals import set_pref
    set_pref("units", "lbs")
    assert cli._parse_height_to_cm("5'10") == 177.8
    assert cli._parse_height_to_cm("5'10\"") == 177.8
    assert cli._parse_height_to_cm("70") == 177.8  # plain inches


def test_fmt_height_units(tmp_db):
    import cli
    from db.goals import set_pref
    set_pref("units", "kg")
    assert cli._fmt_height(178) == "178 cm"
    set_pref("units", "lbs")
    assert cli._fmt_height(178) == "5'10\""


# ── manual entry preserves same-day fields ────────────────────────────────────

def test_save_body_today_merges_same_day(tmp_db):
    import cli
    from db.store import query
    cli._save_body_today(weight_kg=80.0, fat_percent=18.0)
    cli._save_body_today(fat_percent=17.0)  # weight must be preserved
    row = query("SELECT weight_kg, fat_percent FROM body_measurements ORDER BY date DESC LIMIT 1")[0]
    assert row["weight_kg"] == 80.0
    assert row["fat_percent"] == 17.0
