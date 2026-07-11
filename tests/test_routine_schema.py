"""Tests for ai/routine_schema.validate_routine_args — the gate that keeps
malformed AI tool arguments (truncated JSON fragments) away from the UI/DB."""

from ai.routine_schema import validate_routine_args


def _valid_args() -> dict:
    return {
        "title": "Push Day",
        "notes": "Focus on progressive overload",
        "exercises": [
            {
                "exercise_template_id": "94B7239B",
                "title": "Bench Press",
                "notes": "Slow eccentric",
                "rest_seconds": 90,
                "sets": [
                    {"type": "warmup", "weight_kg": 40, "reps": 10},
                    {"type": "normal", "weight_kg": 60.0, "reps": 8},
                ],
            }
        ],
    }


def test_valid_routine_passes_and_normalizes():
    routine, errors = validate_routine_args(_valid_args())
    assert errors == []
    assert routine is not None
    assert routine["title"] == "Push Day"
    ex = routine["exercises"][0]
    assert ex["exercise_template_id"] == "94B7239B"
    assert ex["title"] == "Bench Press"
    assert ex["sets"][0] == {"type": "warmup", "weight_kg": 40.0, "reps": 10}


def test_float_reps_coerced_to_int():
    args = _valid_args()
    args["exercises"][0]["sets"][0]["reps"] = 10.0
    routine, errors = validate_routine_args(args)
    assert errors == []
    assert routine is not None
    assert routine["exercises"][0]["sets"][0]["reps"] == 10


def test_unit_suffix_strings_coerced():
    args = _valid_args()
    args["exercises"][0]["sets"][0]["weight_kg"] = "60kg"
    routine, errors = validate_routine_args(args)
    assert errors == []
    assert routine is not None
    assert routine["exercises"][0]["sets"][0]["weight_kg"] == 60.0


def test_unknown_set_type_coerced_to_normal():
    args = _valid_args()
    args["exercises"][0]["sets"][0]["type"] = "working"
    routine, errors = validate_routine_args(args)
    assert errors == []
    assert routine is not None
    assert routine["exercises"][0]["sets"][0]["type"] == "normal"


def test_json_fragment_in_set_type_rejected():
    # The exact failure mode from truncated tool calls
    args = _valid_args()
    args["exercises"][0]["sets"][1]["type"] = "normal,weight_kg:30},{reps:12,type: "
    routine, errors = validate_routine_args(args)
    assert routine is None
    assert any("type" in e for e in errors)


def test_json_fragment_in_title_rejected():
    args = _valid_args()
    args["title"] = '}]},{exercise_template_id: "abc"'
    routine, errors = validate_routine_args(args)
    assert routine is None
    assert any("title" in e for e in errors)


def test_dict_title_rejected():
    args = _valid_args()
    args["title"] = {"nested": "junk"}
    routine, _errors = validate_routine_args(args)
    assert routine is None


def test_empty_args_rejected():
    # OpenAI-compat path turns unparseable tool arguments into {}
    routine, errors = validate_routine_args({})
    assert routine is None
    assert errors


def test_exercises_as_string_rejected():
    args = _valid_args()
    args["exercises"] = "not a list"
    routine, _errors = validate_routine_args(args)
    assert routine is None


def test_empty_exercises_rejected_for_push():
    args = _valid_args()
    args["exercises"] = []
    routine, _errors = validate_routine_args(args)
    assert routine is None


def test_empty_exercises_allowed_for_update():
    routine, errors = validate_routine_args(
        {"routine_id": "r-1", "title": "Renamed", "exercises": []},
        require_routine_id=True,
    )
    assert errors == []
    assert routine is not None
    assert routine["routine_id"] == "r-1"
    assert routine["exercises"] == []


def test_missing_routine_id_rejected_when_required():
    routine, errors = validate_routine_args(_valid_args(), require_routine_id=True)
    assert routine is None
    assert any("routine_id" in e for e in errors)


def test_non_numeric_weight_rejected():
    args = _valid_args()
    args["exercises"][0]["sets"][0]["weight_kg"] = "heavy-ish"
    routine, _errors = validate_routine_args(args)
    assert routine is None


def test_long_notes_truncated_not_rejected():
    args = _valid_args()
    args["exercises"][0]["notes"] = "x" * 5000
    routine, errors = validate_routine_args(args)
    assert errors == []
    assert routine is not None
    assert len(routine["exercises"][0]["notes"]) <= 1000


def test_wrapped_list_exercise_unwrapped():
    # Some models emit [[{...}]] instead of [{...}]
    args = _valid_args()
    args["exercises"] = [args["exercises"]]
    routine, errors = validate_routine_args(args)
    assert errors == []
    assert routine is not None
    assert routine["exercises"][0]["exercise_template_id"] == "94B7239B"


def test_missing_template_id_rejected():
    args = _valid_args()
    del args["exercises"][0]["exercise_template_id"]
    routine, errors = validate_routine_args(args)
    assert routine is None
    assert any("exercise_template_id" in e for e in errors)
