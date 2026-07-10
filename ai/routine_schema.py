"""Validation gate for AI-generated routine tool arguments.

The model's push_routine / update_routine tool calls arrive as parsed dicts,
but a response truncated at max_tokens (or otherwise malformed) can carry raw
JSON fragments as field values — e.g. a set "type" of
'normal,weight_kg:30},{reps:12,type: '. Nothing downstream should render,
confirm, or persist those, so this module validates and normalizes the args
BEFORE the confirmation UI and the local DB. It is intentionally separate from
hevy.client._sanitize_routine, which shapes the payload for the Hevy API and
silently drops what it can't fix: here garbage must be *rejected* so the model
can be asked to regenerate.
"""

VALID_SET_TYPES = {"warmup", "normal", "failure", "dropset"}

_MAX_TITLE_LEN = 150
_MAX_NOTES_LEN = 1000

# Characters that only show up when raw JSON leaks into a string value.
_JSON_FRAGMENT_MARKERS = ("{", "}", "[", "]", '":', "':")


def validate_routine_args(
    args: dict,
    *,
    require_routine_id: bool = False,
) -> tuple[dict | None, list[str]]:
    """Validate tool args for push_routine / update_routine.

    Returns (normalized_routine, []) on success or (None, [error, ...]) when
    the args are unusable and the model should regenerate them.
    """
    errors: list[str] = []
    if not isinstance(args, dict) or not args:
        return None, ["arguments must be a non-empty object"]

    routine_id = None
    if require_routine_id:
        routine_id = _clean_str(args.get("routine_id"))
        if not routine_id:
            errors.append("routine_id must be a non-empty string")

    title = _clean_str(args.get("title"))
    if not title:
        errors.append("title must be a non-empty string")
    elif _looks_like_json_fragment(title):
        errors.append("title contains raw JSON fragments")

    raw_exercises = args.get("exercises")
    if raw_exercises is None and require_routine_id:
        raw_exercises = []  # updates may touch only title/notes
    if not isinstance(raw_exercises, list):
        errors.append("exercises must be a list")
        raw_exercises = []
    elif not raw_exercises and not require_routine_id:
        errors.append("exercises must be a non-empty list")

    exercises = []
    for i, raw_ex in enumerate(raw_exercises):
        ex, ex_errors = _validate_exercise(raw_ex, i)
        errors.extend(ex_errors)
        if ex is not None:
            exercises.append(ex)

    if errors:
        return None, errors

    routine: dict = {"title": title[:_MAX_TITLE_LEN], "exercises": exercises}
    if routine_id:
        routine["routine_id"] = routine_id
    notes = _clean_str(args.get("notes"))
    if notes:
        routine["notes"] = notes[:_MAX_NOTES_LEN]
    if args.get("folder_id") is not None:
        routine["folder_id"] = args["folder_id"]
    return routine, []


def _validate_exercise(raw, index: int) -> tuple[dict | None, list[str]]:
    label = f"exercises[{index}]"
    # Some models wrap items as [[{...}]] instead of [{...}]
    if isinstance(raw, list):
        raw = raw[0] if raw and isinstance(raw[0], dict) else None
    if not isinstance(raw, dict):
        return None, [f"{label} must be an object"]

    errors: list[str] = []
    tid = _clean_str(raw.get("exercise_template_id"))
    if not tid:
        errors.append(f"{label}.exercise_template_id must be a non-empty string")
    elif _looks_like_json_fragment(tid) or len(tid) > 40:
        errors.append(f"{label}.exercise_template_id is not a valid template id")

    title = _clean_str(raw.get("title"))
    if title and _looks_like_json_fragment(title):
        errors.append(f"{label}.title contains raw JSON fragments")
        title = None

    raw_sets = raw.get("sets")
    if not isinstance(raw_sets, list) or not raw_sets:
        errors.append(f"{label}.sets must be a non-empty list")
        raw_sets = []

    sets = []
    for j, raw_set in enumerate(raw_sets):
        st, set_errors = _validate_set(raw_set, f"{label}.sets[{j}]")
        errors.extend(set_errors)
        if st is not None:
            sets.append(st)

    if errors:
        return None, errors

    ex: dict = {"exercise_template_id": tid, "sets": sets}
    if title:
        ex["title"] = title[:_MAX_TITLE_LEN]
    notes = _clean_str(raw.get("notes"))
    if notes:
        ex["notes"] = notes[:_MAX_NOTES_LEN]
    rest, err = _num(raw.get("rest_seconds"), int)
    if err is None and rest is not None:
        ex["rest_seconds"] = rest
    if raw.get("superset_id") is not None:
        sid, err = _num(raw.get("superset_id"), int)
        if sid is not None:
            ex["superset_id"] = sid
    return ex, []


def _validate_set(raw, label: str) -> tuple[dict | None, list[str]]:
    if isinstance(raw, list):
        raw = raw[0] if raw and isinstance(raw[0], dict) else None
    if not isinstance(raw, dict):
        return None, [f"{label} must be an object"]

    errors: list[str] = []
    set_type = raw.get("type", "normal")
    if not isinstance(set_type, str):
        set_type = "normal"
    else:
        set_type = set_type.strip().lower()
        if set_type not in VALID_SET_TYPES:
            # A short unknown word ("working", "top") is a harmless synonym —
            # coerce to normal. Anything long or with JSON punctuation is leakage.
            if _looks_like_json_fragment(set_type) or len(set_type) > 20:
                errors.append(f"{label}.type contains invalid data")
            set_type = "normal"

    result: dict = {"type": set_type}
    for field, kind in (
        ("weight_kg", float),
        ("reps", int),
        ("duration_seconds", int),
        ("distance_meters", int),
        ("custom_metric", float),
    ):
        if raw.get(field) is None:
            continue
        value, err = _num(raw[field], kind)
        if err:
            errors.append(f"{label}.{field} {err}")
        elif value is not None:
            result[field] = value

    if errors:
        return None, errors
    return result, []


def _clean_str(v) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip()
    return s or None


def _looks_like_json_fragment(s: str) -> bool:
    return any(marker in s for marker in _JSON_FRAGMENT_MARKERS)


def _num(v, kind) -> tuple[int | float | None, str | None]:
    """Coerce to int/float; strings may carry trailing units ('60kg', '90s')."""
    if v is None:
        return None, None
    if isinstance(v, bool):
        return None, "must be a number"
    if isinstance(v, str):
        stripped = v.strip().lower()
        for unit in ("kg", "lbs", "lb", "s", "m", "x"):
            if stripped.endswith(unit):
                stripped = stripped[: -len(unit)].strip()
                break
        v = stripped
    try:
        return kind(float(v)), None
    except (TypeError, ValueError):
        return None, "must be a number"
