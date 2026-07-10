"""Goals — the goals wizard, weekly check-in, progress rendering, and menu action."""

from typing import Any

import questionary
from rich.markup import escape as _esc
from rich.panel import Panel

from analytics.goal_progress import compute_goal_progress
from analytics.records import current_e1rm_kg
from commands._shared import _dlog
from db.goals import (
    clear_goals,
    get_goals,
    get_pref,
    mark_goals_asked,
    save_goal,
    set_pref,
)
from db.store import query
from i18n import _
from ui.console import STYLE, console
from ui.console import score_color as _score_color
from ui.format import fmt_weight as _fmt_weight
from ui.format import get_units as _get_units
from ui.prompts import confirm_destructive, number_validator
from ui.prompts import is_number as _is_number_str
from ui.widgets import bar as _mk_bar

# ── goals wizard ──────────────────────────────────────────────────────────────


def _wizard_lift_prs() -> None:
    exercises = query("SELECT id, title FROM exercise_templates ORDER BY title")
    if not exercises:
        console.print(_("wizard.no_exercises"))
        return
    names = [e["title"] for e in exercises]
    id_by_name = {e["title"]: e["id"] for e in exercises}
    units = _get_units()

    console.print(_("wizard.lift_hint"))

    while True:
        name = questionary.autocomplete(
            _("wizard.lift_exercise_prompt"),
            choices=names,
            style=STYLE,
        ).ask()
        if not name or name not in id_by_name:
            break

        template_id = id_by_name[name]
        current_display = _fmt_weight(current_e1rm_kg(template_id))

        target_str = questionary.text(
            _("wizard.lift_target_prompt", units=units, current=current_display),
            style=STYLE,
            validate=lambda v: (v == "" or _is_number_str(v)) or _("validate.enter_number"),
        ).ask()
        if not target_str:
            break
        target_input = float(target_str)
        target_kg = round(target_input / 2.20462, 2) if units == "lbs" else target_input
        target_label = f"{int(target_input)} {units}"
        save_goal(
            type="lift_pr",
            description=f"{name} — {target_label}",
            target=target_kg,
            unit="kg",
            exercise_template_id=template_id,
            exercise_name=name,
        )
        _dlog("GOAL", "Goal created", type="lift_pr")
        console.print(_("wizard.lift_saved", name=name, target=target_label))

        if not questionary.confirm(_("wizard.lift_add_another"), default=False, style=STYLE).ask():
            break


def _wizard_frequency() -> None:
    choice = questionary.select(
        _("wizard.frequency_prompt"),
        choices=["2", "3", "4", "5", "6"],
        default="4",
        style=STYLE,
    ).ask()
    if choice:
        target = int(choice)
        save_goal(type="frequency", description=f"Train {target}× per week", target=target, unit="sessions/wk")
        _dlog("GOAL", "Goal created", type="frequency", target=f"{target}x/wk")
        console.print(_("wizard.frequency_saved", target=target))


def _wizard_weight(goal_type: str) -> None:
    rows = query("SELECT weight_kg FROM body_measurements WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT 1")
    current_kg = rows[0]["weight_kg"] if rows else None
    units = _get_units()
    hint = f" (current: {_fmt_weight(current_kg)})" if current_kg else ""

    target_str = questionary.text(
        _("wizard.weight_target_prompt", units=units, hint=hint),
        style=STYLE,
        validate=number_validator(_("validate.enter_number")),
    ).ask()
    if not target_str:
        return
    target_input = float(target_str)
    target_kg = round(target_input / 2.20462, 2) if units == "lbs" else target_input
    target_label = f"{target_input} {units}"
    direction = "Lose" if goal_type == "weight_loss" else "Gain"
    save_goal(
        type=goal_type,
        description=f"{direction} weight to {target_label}",
        target=target_kg,
        unit="kg",
        start_value=current_kg,
    )
    _dlog("GOAL", "Goal created", type=goal_type)
    console.print(_("wizard.weight_saved", direction=direction, target=target_label))


def _wizard_body_fat() -> None:
    rows = query("SELECT fat_percent FROM body_measurements WHERE fat_percent IS NOT NULL ORDER BY date DESC LIMIT 1")
    current = rows[0]["fat_percent"] if rows else None
    hint = f" (current: {current}%)" if current else ""

    target_str = questionary.text(
        _("wizard.body_fat_prompt", hint=hint),
        style=STYLE,
        validate=number_validator(_("validate.enter_number")),
    ).ask()
    if not target_str:
        return
    target = float(target_str)
    save_goal(
        type="body_fat",
        description=f"Reach {target}% body fat",
        target=target,
        unit="%",
        start_value=current,
    )
    _dlog("GOAL", "Goal created", type="body_fat")
    console.print(_("wizard.body_fat_saved", target=target))


def _wizard_volume() -> None:
    muscles = [
        "chest",
        "lats",
        "upper_back",
        "shoulders",
        "biceps",
        "triceps",
        "quadriceps",
        "hamstrings",
        "glutes",
        "calves",
        "abdominals",
    ]
    muscle = questionary.select(_("wizard.volume_muscle_prompt"), choices=muscles, style=STYLE).ask()
    if not muscle:
        return
    target_str = questionary.text(
        _("wizard.volume_sets_prompt", muscle=muscle),
        style=STYLE,
        validate=lambda v: v.isdigit() or _("validate.enter_whole_number"),
    ).ask()
    if not target_str:
        return
    target = float(target_str)
    save_goal(
        type="volume",
        description=f"{muscle.replace('_', ' ').title()} — {int(target)} sets/week",
        target=target,
        unit="sets/wk",
        muscle_group=muscle,
    )
    _dlog("GOAL", "Goal created", type="volume", muscle=muscle, target=f"{int(target)} sets/wk")
    console.print(_("wizard.volume_saved"))


def _wizard_custom() -> None:
    text = questionary.text(
        _("wizard.custom_prompt"),
        style=STYLE,
        validate=lambda v: len(v.strip()) > 3 or _("validate.describe_goal"),
    ).ask()
    if text:
        save_goal(type="custom", description=text.strip())
        _dlog("GOAL", "Goal created", type="custom")
        console.print(_("wizard.custom_saved"))


def run_goals_wizard(is_update: bool = False) -> None:
    name = get_pref("display_name")
    if not name:
        console.print()
        name = questionary.text(
            _("wizard.name_prompt"),
            validate=lambda v: bool(v.strip()) or _("validate.name_required"),
            style=STYLE,
        ).ask()
        if name:
            set_pref("display_name", name.strip())
        else:
            name = _("wizard.name_fallback")  # cancelled prompt must not greet 'None'

    greet = _("wizard.greeting_update", name=name) if is_update else _("wizard.greeting_new", name=name)
    console.print(f"\n  [bold cyan]{greet}[/bold cyan]\n")

    selected = questionary.checkbox(
        _("wizard.select_goals"),
        choices=[
            questionary.Choice(_("wizard.goal_lift"), value="lift_pr"),
            questionary.Choice(_("wizard.goal_frequency"), value="frequency"),
            questionary.Choice(_("wizard.goal_weight_loss"), value="weight_loss"),
            questionary.Choice(_("wizard.goal_weight_gain"), value="weight_gain"),
            questionary.Choice(_("wizard.goal_body_fat"), value="body_fat"),
            questionary.Choice(_("wizard.goal_volume"), value="volume"),
            questionary.Choice(_("wizard.goal_custom"), value="custom"),
        ],
        style=STYLE,
    ).ask()

    if not selected:
        return

    if is_update:
        clear_goals()

    for goal_type in selected:
        if goal_type == "lift_pr":
            _wizard_lift_prs()
        elif goal_type == "frequency":
            _wizard_frequency()
        elif goal_type == "weight_loss":
            _wizard_weight("weight_loss")
        elif goal_type == "weight_gain":
            _wizard_weight("weight_gain")
        elif goal_type == "body_fat":
            _wizard_body_fat()
        elif goal_type == "volume":
            _wizard_volume()
        elif goal_type == "custom":
            _wizard_custom()

    mark_goals_asked()
    total = len(get_goals())
    _dlog("GOAL", "Goals wizard completed", total=total, mode="update" if is_update else "new")
    console.print(_("wizard.n_saved", total=total))


def _weekly_checkin() -> None:
    goals = get_goals()
    if not goals:
        console.print()
        run_goals_wizard()
        return

    name = get_pref("display_name") or "there"
    console.print(_("weekly.checkin_title", name=_esc(name)))
    console.print(_("weekly.current_goals"))
    for g in goals:
        console.print(f"    [dim]•[/dim] {_esc(g['description'])}")
    console.print()

    answer = questionary.select(
        _("weekly.still_same"),
        choices=[
            questionary.Choice(_("weekly.keep"), value="keep"),
            questionary.Choice(_("weekly.update"), value="update"),
            questionary.Choice(_("weekly.skip"), value="skip"),
        ],
        style=STYLE,
    ).ask()

    if answer == "update":
        _dlog("GOAL", "Weekly check-in: user chose to update goals")
        run_goals_wizard(is_update=True)
    elif answer == "keep":
        _dlog("GOAL", "Weekly check-in: goals confirmed")
        mark_goals_asked()
        console.print(_("weekly.confirmed"))
    elif answer == "skip":
        _dlog("GOAL", "Weekly check-in: skipped")
    # skip: don't update the timestamp so we ask again next run


# ── goal progress rendering ───────────────────────────────────────────────────


def _render_goals_progress() -> None:
    progress = compute_goal_progress()
    if not progress:
        return

    lines = []
    for g in progress:
        if g["achieved"]:
            lines.append(_("goals.achieved", description=g["description"]))
            lines.append("")
            continue

        pct = g.get("pct")
        if pct is None:
            lines.append(f"  [dim]◦[/dim] [bold]{g['description']}[/bold]  {_('goals.custom_label')}")
            lines.append("")
            continue

        pct = float(pct)
        bar_color = _score_color(max(0, int(pct)))

        bar = _mk_bar(max(0.0, pct), 100, 22, bar_color)

        current = g.get("current")
        target = g.get("target")
        start = g.get("start")
        unit = g.get("unit", "")

        def _fmt_val(v: Any, unit: Any = unit) -> str:
            return _fmt_weight(v) if unit == "kg" else f"{v}{unit}" if unit else f"{v}"

        if current is not None and target is not None:
            if start is not None:
                # Body metrics (weight / body fat): show initial → current → target.
                detail = f"  {_fmt_val(start)} → {_fmt_val(current)} → {_fmt_val(target)}  ({pct:.0f}%)"
            else:
                detail = f"  {_fmt_val(current)} → {_fmt_val(target)}  ({pct:.0f}%)"
        else:
            detail = f"  {pct:.0f}%"

        lines.append(f"  [bold]{g['description']}[/bold]")
        lines.append(f"  {bar}{detail}")
        lines.append("")

    if lines:
        console.print(
            Panel(
                "\n".join(lines).rstrip(),
                title=_("goals.progress_title"),
                border_style="yellow",
                padding=(0, 1),
            )
        )


def _do_goals() -> None:
    goals = get_goals()
    action = questionary.select(
        _("goals.menu_prompt"),
        choices=[
            questionary.Choice(_("goals.view_label"), value="view"),
            questionary.Choice(_("goals.update_label"), value="update"),
            questionary.Choice(_("goals.reset_label"), value="reset"),
        ],
        style=STYLE,
    ).ask()
    if not action:
        return
    _dlog("MENU", "Goals action selected", action=action, active_goals=len(goals))
    if action == "view":
        if not goals:
            console.print(_("goals.none_yet"))
            if questionary.confirm(_("goals.set_now"), default=True, style=STYLE).ask():
                run_goals_wizard()
        else:
            _render_goals_progress()
    elif action == "update":
        run_goals_wizard(is_update=True)
    elif action == "reset" and confirm_destructive(_("goals.clear_confirm"), double=True):
        clear_goals()
        _dlog("GOAL", "Goals cleared and wizard restarted")
        run_goals_wizard()
