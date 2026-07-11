"""Value formatting and typed preference parsing for the CLI layer."""

from datetime import UTC, datetime

from db.goals import get_pref
from i18n import _

KG_PER_LB = 2.20462
CM_PER_IN = 2.54


def get_units() -> str:
    return get_pref("units") or "kg"


def kg_to_lbs(kg: float) -> float:
    return round(float(kg) * KG_PER_LB, 1)


def lbs_to_kg(lbs: float) -> float:
    return round(float(lbs) / KG_PER_LB, 2)


def _trim_number(v: float) -> int | float:
    return int(v) if v == int(v) else v


def fmt_weight(kg_val: float | None) -> str:
    if kg_val is None:
        return "—"
    val = float(kg_val)
    if get_units() == "lbs":
        return f"{_trim_number(kg_to_lbs(val))} lbs"
    return f"{_trim_number(val)} kg"


def fmt_height(cm_val: float | None) -> str:
    """Format a height (stored in cm) per the user's unit preference."""
    if cm_val is None:
        return "—"
    cm = float(cm_val)
    if get_units() == "lbs":  # imperial → feet'inches"
        total_in = cm / CM_PER_IN
        feet = int(total_in // 12)
        inches = round(total_in - feet * 12)
        if inches == 12:  # rounding spill-over
            feet += 1
            inches = 0
        return f"{feet}'{inches}\""
    return f"{int(cm) if cm == int(cm) else round(cm, 1)} cm"


def parse_height_to_cm(raw: str) -> float | None:
    """Parse a height entered as cm (metric) or feet/inches (imperial) into cm.

    Imperial accepts: 5'11, 5'11", 5 11, or a plain number of inches (e.g. 71).
    Metric accepts a plain number of cm.
    """
    s = (raw or "").strip().replace('"', "").replace("”", "").replace("’", "'")
    if not s:
        return None
    try:
        if get_units() == "lbs":
            if "'" in s or " " in s:
                parts = s.replace("'", " ").split()
                feet = float(parts[0])
                inches = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
                return round((feet * 12 + inches) * CM_PER_IN, 1)
            # plain number → inches
            return round(float(s) * CM_PER_IN, 1)
        return round(float(s), 1)  # metric: cm
    except (ValueError, IndexError):
        return None


def time_ago(iso_str: str) -> str:
    """Localized compact '5m ago'-style age of an ISO timestamp."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        secs = int((datetime.now(UTC) - dt).total_seconds())
        if secs < 60:
            return _("time_ago.just_now")
        if secs < 3600:
            return _("time_ago.minutes", n=secs // 60)
        if secs < 86400:
            return _("time_ago.hours", n=secs // 3600)
        return _("time_ago.days", n=secs // 86400)
    except Exception:
        return _("time_ago.unknown")


def fmt_duration(start_iso: str, end_iso: str) -> str:
    try:
        s = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        return f"{int((e - s).total_seconds() / 60)} min"
    except Exception:
        return ""


def get_int_pref(key: str, default: int, *, allowed: tuple[int, ...] | None = None, minimum: int | None = None) -> int:
    """Read an integer preference with fallback and optional constraints."""
    try:
        value = int(get_pref(key) or default)
    except (TypeError, ValueError):
        return default
    if allowed is not None and value not in allowed:
        return default
    if minimum is not None:
        value = max(minimum, value)
    return value


def get_bool_pref(key: str, default: bool = True) -> bool:
    """The '!= \"0\"' preference idiom, named: unset counts as `default`."""
    raw = get_pref(key)
    if raw is None:
        return default
    return raw != "0"


def parse_weeks_value(value: str, default: int = 8) -> int:
    """Parse the canonical 'N weeks' choice/pref string into its integer N."""
    try:
        return int(str(value).split()[0])
    except (ValueError, IndexError):
        return default
