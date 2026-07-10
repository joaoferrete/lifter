"""Body measurements — manual entry, onboarding baseline, and the periodic check-in."""

from datetime import UTC, datetime

import questionary
from rich.panel import Panel

from analytics.records import body_measurement_trend, compute_bmi
from commands._shared import _dlog
from db.goals import _invalidate_render_cache, get_height_cm, get_pref, set_pref
from db.store import query, upsert_body_measurement
from i18n import _
from ui.console import STYLE, console
from ui.format import fmt_height as _fmt_height
from ui.format import fmt_weight as _fmt_weight
from ui.format import get_units as _get_units
from ui.format import lbs_to_kg as _lbs_to_kg
from ui.format import parse_height_to_cm as _parse_height_to_cm


def _is_number(v: str) -> bool:
    return v.strip().replace(".", "", 1).isdigit()


def _save_body_today(weight_kg: float | None = None, fat_percent: float | None = None) -> None:
    """Upsert today's body_measurements row, preserving other fields already set today."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    existing = query("SELECT * FROM body_measurements WHERE date = ?", (today,))
    row = dict(existing[0]) if existing else {}
    row["date"] = today
    if weight_kg is not None:
        row["weight_kg"] = weight_kg
    if fat_percent is not None:
        row["fat_percent"] = fat_percent
    upsert_body_measurement(row)
    _invalidate_render_cache()
    _dlog("BODY", "Body measurement saved", date=today, weight_kg=weight_kg, fat_percent=fat_percent)


def _prompt_weight_kg() -> float | None:
    """Prompt for a weight in the user's unit; return it converted to kg (or None)."""
    units = _get_units()
    raw = questionary.text(
        _("body.weight_prompt", units=units),
        style=STYLE,
        validate=lambda v: (not v.strip()) or _is_number(v) or _("validate.enter_number"),
    ).ask()
    if not raw or not raw.strip():
        return None
    val = float(raw)
    return _lbs_to_kg(val) if units == "lbs" else val


def _prompt_and_save_height() -> bool:
    """Prompt for height (unit-aware) and store it as the per-profile height_cm pref."""
    units = _get_units()
    prompt_key = "body.height_prompt_imperial" if units == "lbs" else "body.height_prompt_metric"
    raw = questionary.text(_(prompt_key), style=STYLE).ask()
    if not raw or not raw.strip():
        return False
    cm = _parse_height_to_cm(raw)
    if cm is None or cm <= 0:
        console.print(_("body.height_invalid"))
        return False
    set_pref("height_cm", str(cm))
    _dlog("BODY", "Height set", height_cm=cm)
    console.print(_("body.height_saved", height=_fmt_height(cm)))
    return True


def _do_body_entry() -> None:
    """Manually record current weight / body-fat and show BMI. Main-menu action."""
    console.clear()

    body = body_measurement_trend(8)
    cur_w, cur_f = body.get("weight_kg"), body.get("fat_percent")
    height_cm = get_height_cm()
    info = []
    if cur_w:
        info.append(_("body.current_weight", weight=_fmt_weight(cur_w)))
    if cur_f:
        info.append(_("body.current_fat", fat=cur_f))
    if height_cm:
        info.append(_("body.current_height", height=_fmt_height(height_cm)))
    bmi = compute_bmi(cur_w, height_cm)
    if bmi is not None:
        info.append(_("body.current_bmi", bmi=bmi))
    if info:
        console.print(Panel("\n".join(info), title=_("body.panel_title"), border_style="cyan", padding=(0, 2)))

    weight_kg = _prompt_weight_kg()
    f_raw = questionary.text(
        _("body.fat_prompt"),
        style=STYLE,
        validate=lambda v: (not v.strip()) or _is_number(v) or _("validate.enter_number"),
    ).ask()
    fat = float(f_raw) if f_raw and f_raw.strip() else None

    if weight_kg is None and fat is None:
        console.print(_("body.nothing_entered"))
        return

    _save_body_today(weight_kg=weight_kg, fat_percent=fat)

    # Height is needed for BMI — offer to set it if still unknown.
    if get_height_cm() is None:
        _prompt_and_save_height()

    new_bmi = compute_bmi(weight_kg if weight_kg is not None else cur_w, get_height_cm())
    if new_bmi is not None:
        console.print(_("body.saved_with_bmi", bmi=new_bmi))
    else:
        console.print(_("body.saved"))


def _onboard_body_metrics() -> None:
    """First-run baseline: ask height + current weight (Google Fit may not be connected)."""
    console.print()
    console.print(_("body.onboard_intro"))
    _prompt_and_save_height()
    weight_kg = _prompt_weight_kg()
    if weight_kg is not None:
        _save_body_today(weight_kg=weight_kg)
    set_pref("weight_last_asked", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _check_body_checkin() -> None:
    """Ask for current weight when the latest reading is stale; ask height once if unset."""
    try:
        cadence = int(get_pref("goals_checkin_days") or 7)
    except (TypeError, ValueError):
        cadence = 7

    need_height = get_height_cm() is None

    rows = query("SELECT date FROM body_measurements WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT 1")
    stale = True
    if rows:
        try:
            last = datetime.fromisoformat(rows[0]["date"]).date()
            stale = (datetime.now(UTC).date() - last).days >= cadence
        except Exception:
            stale = True

    # Don't re-prompt within the cadence even if the user declined last time.
    asked_recently = False
    last_asked = get_pref("weight_last_asked")
    if last_asked:
        try:
            asked = datetime.fromisoformat(last_asked.replace("Z", "+00:00"))
            asked_recently = (datetime.now(UTC) - asked).days < cadence
        except Exception:
            asked_recently = False

    if not need_height and (not stale or asked_recently):
        return

    console.print()
    if need_height:
        _prompt_and_save_height()
    if stale and not asked_recently:
        if questionary.confirm(_("body.update_weight_prompt"), default=True, style=STYLE).ask():
            weight_kg = _prompt_weight_kg()
            if weight_kg is not None:
                _save_body_today(weight_kg=weight_kg)
                console.print(_("body.saved"))
        set_pref("weight_last_asked", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
