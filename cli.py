"""hevy — interactive personal Hevy workout client."""

import contextlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import questionary
from rich import box
from rich.markup import escape as _esc
from rich.panel import Panel
from rich.table import Table

import config
from analytics.e1rm import NORMAL_SET_FILTER_SQL, e1rm_sql
from analytics.frequency import muscle_group_frequency, workout_frequency
from analytics.goal_progress import compute_goal_progress
from analytics.progression import detect_plateaus, exercise_progression, top_progressions
from analytics.records import all_time_records, body_measurement_trend, recent_prs
from analytics.volume import muscle_group_summary, sets_per_muscle_per_week, weekly_volume

# AI_PROVIDER/AI_MODEL are read as config.X — apply_ai_overrides() mutates them
# at runtime, so an import-by-name here would go stale.
from config import get_provider_api_key
from db.goals import (
    clear_goals,
    get_goals,
    get_pref,
    mark_goals_asked,
    mark_report_generated,
    save_goal,
    set_pref,
    should_ask_goals,
    should_auto_report,
)
from db.store import init_db, query
from hevy.client import HevyClient
from hevy.sync import full_sync, incremental_sync
from i18n import _
from ui.console import STYLE, console
from ui.console import score_color as _score_color
from ui.format import (
    fmt_duration as _fmt_duration,
)
from ui.format import (
    fmt_height as _fmt_height,
)
from ui.format import (
    fmt_weight as _fmt_weight,
)
from ui.format import (
    get_int_pref,
)
from ui.format import (
    get_units as _get_units,
)
from ui.format import (
    lbs_to_kg as _lbs_to_kg,
)
from ui.format import (
    parse_height_to_cm as _parse_height_to_cm,
)
from ui.format import (
    time_ago as _time_ago,
)
from ui.prompts import (
    confirm_destructive,
    number_validator,
)
from ui.prompts import (
    day_choices as _day_choices,
)
from ui.prompts import (
    is_number as _is_number_str,
)
from ui.prompts import (
    week_choices as _week_choices,
)
from ui.widgets import score_bar as _fmt_score_bar

# ── helpers ───────────────────────────────────────────────────────────────────


def _app_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("lifter-cli")  # distribution name, not a module name
    except PackageNotFoundError:
        return "dev"


def _dlog(category: str, msg: str, **kv) -> None:
    """Forward to debug_log.log without ever raising."""
    try:
        import debug_log

        debug_log.log(category, msg, **kv)
    except Exception:
        pass


def _sync_status_str(key: str) -> str:
    """One-line summary of the last recorded sync outcome for the dev panel."""
    from db.store import get_sync_result

    result = get_sync_result(key)
    if not result:
        return _("settings.dev.sync_never")
    ago = _time_ago(result.get("when", ""))
    detail = _esc(result.get("detail", ""))
    if result.get("ok"):
        return _("settings.dev.sync_ok", ago=ago, detail=detail)
    return _("settings.dev.sync_failed", ago=ago, detail=detail)


def _require_hevy() -> HevyClient | None:
    if not config.HEVY_API_KEY:
        console.print(_("error.hevy_api_key_not_set"))
        return None
    return HevyClient()


def _is_placeholder_key(value: str) -> bool:
    """True if the key is still a .env.example placeholder (e.g. 'sk-or-your-key')."""
    return "your-" in value.lower()


def _provider_key_ok(provider: str) -> bool:
    """Does this provider have a usable credential configured in .env?"""
    if provider == "bedrock":
        return bool(config.AWS_BEARER_TOKEN_BEDROCK or (config.AWS_ACCESS_KEY_ID and config.AWS_SECRET_ACCESS_KEY))
    key = get_provider_api_key(provider)
    return bool(key) and not _is_placeholder_key(key)


def _ai_configured() -> bool:
    """Silent check: is the current AI provider usable? (prints nothing)."""
    return config.AI_PROVIDER in config.KNOWN_PROVIDERS and _provider_key_ok(config.AI_PROVIDER)


def _require_ai() -> bool:
    if config.AI_PROVIDER not in config.KNOWN_PROVIDERS:
        valid = ", ".join(sorted(config.KNOWN_PROVIDERS))
        console.print(_("error.ai_provider_unknown", provider=config.AI_PROVIDER, valid=valid))
        return False
    if config.AI_PROVIDER == "bedrock":
        # Either a bearer token (no boto3 needed) or AWS access keys.
        if _provider_key_ok("bedrock"):
            return True
        console.print(_("error.ai_bedrock_no_creds"))
        return False
    key = get_provider_api_key()
    if not key or _is_placeholder_key(key):
        key_names = {
            "gemini": "GEMINI_API_KEY",
            "claude": "ANTHROPIC_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "groq": "GROQ_API_KEY",
            "github": "GITHUB_TOKEN",
        }
        var = key_names.get(config.AI_PROVIDER, "the relevant API key")
        console.print(_("error.ai_key_not_set", var=var, provider=config.AI_PROVIDER))
        return False
    return True


# Menu actions that manage their own screen pacing return this sentinel;
# any other return value means the main loop should _pause() so output
# (e.g. a guard-failure error) stays visible before the next console.clear().
NO_PAUSE = object()


def _pause():
    from ui.prompts import pause

    pause()


# ── unit helpers ──────────────────────────────────────────────────────────────


def _report_weeks() -> int:
    """Weeks of history for coaching reports (pref `report_weeks`)."""
    return get_int_pref("report_weeks", 8, allowed=(4, 8, 12))


def _stale_seconds() -> int:
    """Age after which synced data counts as stale (pref `sync_stale_hours`)."""
    return get_int_pref("sync_stale_hours", 24, minimum=1) * 3600


# ── score & muscle-distribution helpers ──────────────────────────────────────

_MUSCLE_GROUPS: dict = {
    "Chest": ["chest", "pectorals"],
    "Back": ["lats", "upper_back", "lower_back", "trapezius"],
    "Legs": ["quadriceps", "hamstrings", "glutes", "calves", "hip_flexors"],
    "Shoulders": ["shoulders", "deltoids"],
    "Arms": ["biceps", "triceps", "forearms"],
    "Core": ["abdominals", "core", "obliques"],
    "Cardio": ["cardio", "full_body"],
}


def _sets_by_group(weeks: int = 4) -> dict:
    from render_cache import cached

    return cached(f"sets_by_group:{weeks}", lambda: _sets_by_group_uncached(weeks))


def _sets_by_group_uncached(weeks: int = 4) -> dict:
    spw = sets_per_muscle_per_week(weeks)
    groups: dict = {}
    other = 0.0
    for muscle, s in spw.items():
        placed = False
        for group, muscles in _MUSCLE_GROUPS.items():
            if muscle.lower() in muscles:
                groups[group] = groups.get(group, 0.0) + float(s)
                placed = True
                break
        if not placed:
            other += float(s)
    if other > 0:
        groups["Other"] = other
    return groups


def _render_snapshot_panel() -> None:
    """Compact at-a-glance panel shown below the header on the main menu."""
    lines = []

    # Scores from last AI report
    ws_raw = get_pref("last_workout_score")
    hs_raw = get_pref("last_health_score")
    cs_raw = get_pref("last_combined_score")
    if ws_raw or cs_raw:
        score_lines = []
        if ws_raw:
            score_lines.append(_fmt_score_bar(_("score.training"), int(ws_raw)))
        if hs_raw:
            score_lines.append(_fmt_score_bar(_("score.health"), int(hs_raw)))
        if cs_raw:
            score_lines.append(_fmt_score_bar(_("score.overall"), int(cs_raw)))
        lines.append(_("snapshot.scores_title"))
        lines.extend(score_lines)
        lines.append("")

    # Volume distribution by group (last 4 weeks)
    groups = _sets_by_group(4)
    if groups:
        total = sum(groups.values()) or 1
        max_s = max(groups.values())
        dist_parts = []
        for grp, s in sorted(groups.items(), key=lambda x: -x[1]):
            pct = s / total * 100
            bw = max(1, int(s / max_s * 0.7 * 6))
            dist_parts.append(f"[bold]{grp}[/bold] [cyan]{'█' * bw}[/cyan] {pct:.0f}%")
        lines.append(_("snapshot.volume_title"))
        lines.append("  ".join(dist_parts))
        lines.append("")

    # Body: latest weight + BMI (when height is known)
    from analytics.records import compute_bmi
    from db.goals import get_height_cm

    body = body_measurement_trend(8)
    if body.get("weight_kg"):
        body_parts = [f"[bold]{_fmt_weight(body['weight_kg'])}[/bold]"]
        if body.get("fat_percent"):
            body_parts.append(f"{body['fat_percent']}% {_('snapshot.body_fat_short')}")
        bmi = compute_bmi(body.get("weight_kg"), get_height_cm())
        if bmi is not None:
            body_parts.append(f"{_('snapshot.bmi_short')} {bmi}")
        lines.append(_("snapshot.body_title"))
        lines.append("  " + "  ·  ".join(body_parts))
        lines.append("")

    # Compact goal progress
    from analytics.goal_progress import compute_goal_progress

    progress = compute_goal_progress()
    numeric = [g for g in progress if g.get("pct") is not None and not g["achieved"]]
    achieved = [g for g in progress if g["achieved"]]
    if numeric or achieved:
        lines.append(_("snapshot.goals_title"))
        for g in numeric[:4]:
            pct = float(g["pct"])
            color = _score_color(max(0, int(pct)))
            bw = max(0, min(8, int(pct / 100 * 8)))
            bar = f"[{color}]{'█' * bw}[/{color}][dim]{'░' * (8 - bw)}[/dim]"
            desc = g["description"][:30]
            lines.append(f"  {bar} [{color}]{pct:.0f}%[/{color}]  [dim]{desc}[/dim]")
        if len(numeric) > 4:
            lines.append(_("snapshot.n_more_goals", count=len(numeric) - 4))
        for g in achieved[:2]:
            lines.append(f"  [bold green]✓[/bold green] [dim]{g['description'][:35]}[/dim]")
        custom = [g for g in progress if g.get("pct") is None and not g["achieved"]]
        for g in custom[:2]:
            lines.append(f"  [dim]◦ {g['description'][:35]} (custom)[/dim]")

    if not lines:
        return

    console.print(
        Panel(
            "\n".join(lines).strip(),
            title=_("snapshot.panel_title"),
            border_style="dim",
            padding=(0, 2),
        )
    )
    console.print()


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
        rows = query(
            f"""SELECT MAX({e1rm_sql()}) as e1rm
               FROM workout_sets ws WHERE ws.exercise_template_id = ?
               AND {NORMAL_SET_FILTER_SQL}""",
            (template_id,),
        )
        current_e1rm_kg = round(rows[0]["e1rm"], 1) if rows and rows[0]["e1rm"] else 0
        current_display = _fmt_weight(current_e1rm_kg)

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
        from ui.widgets import bar as _mk_bar

        bar = _mk_bar(max(0.0, pct), 100, 22, bar_color)

        current = g.get("current")
        target = g.get("target")
        start = g.get("start")
        unit = g.get("unit", "")

        def _fmt_val(v, unit=unit) -> str:
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


# ── sync report helpers ───────────────────────────────────────────────────────


def _render_workout_cards(workout_ids: list[str]) -> None:
    for wid in workout_ids:
        rows = query("SELECT * FROM workouts WHERE id = ?", (wid,))
        if not rows:
            continue
        w = rows[0]
        try:
            start_dt = datetime.fromisoformat(w["start_time"].replace("Z", "+00:00"))
            date_str = start_dt.strftime("%a %b %d, %Y")
        except Exception:
            date_str = w.get("start_time", "")[:10]

        duration = _fmt_duration(w.get("start_time", ""), w.get("end_time", ""))
        exercises = query(
            f"""SELECT we.title, we.exercise_template_id,
                      ws.weight_kg, ws.reps,
                      {e1rm_sql()} as e1rm
               FROM workout_exercises we
               JOIN workout_sets ws ON ws.workout_exercise_id = we.id
               WHERE we.workout_id = ? AND {NORMAL_SET_FILTER_SQL}
               ORDER BY we.idx, e1rm DESC""",
            (wid,),
        )
        best: dict[str, dict] = {}
        for ex in exercises:
            n = ex["title"]
            if n not in best or ex["e1rm"] > best[n]["e1rm"]:
                best[n] = ex

        lines = []
        for name, ex in best.items():
            prev = query(
                f"""SELECT MAX({e1rm_sql()}) as top
                   FROM workout_sets ws WHERE ws.exercise_template_id = ?
                   AND {NORMAL_SET_FILTER_SQL} AND ws.workout_id != ?""",
                (ex["exercise_template_id"], wid),
            )
            is_pr = ex["e1rm"] > (prev[0]["top"] or 0) if prev else False
            pr_badge = "  [bold yellow]★ PR[/bold yellow]" if is_pr else ""
            lines.append(f"  [bold]{name}[/bold]  {_fmt_weight(ex['weight_kg'])} × {ex['reps']} reps{pr_badge}")

        if not lines:
            bw = query("SELECT DISTINCT we.title FROM workout_exercises we WHERE we.workout_id = ?", (wid,))
            lines = [f"  {e['title']}" for e in bw]

        console.print(
            Panel(
                "\n".join(lines) if lines else "  (no sets logged)",
                title=f"[bold cyan]{w['title']}[/bold cyan]  [dim]{date_str} · {duration}[/dim]",
                border_style="cyan",
                padding=(0, 1),
            )
        )


def _render_volume_delta() -> None:
    df = weekly_volume(2)
    if df.empty:
        return
    weeks_sorted = sorted(df["week"].unique())
    if len(weeks_sorted) < 2:
        return
    prev_ser = df[df["week"] == weeks_sorted[-2]].set_index("muscle")["volume_kg"]
    curr_ser = df[df["week"] == weeks_sorted[-1]].set_index("muscle")["volume_kg"]
    if curr_ser.empty:
        return
    max_vol = float(curr_ser.max()) or 1.0
    console.print(_("stats.volume_rule_this_week"))
    for muscle in curr_ser.index:
        curr = float(curr_ser[muscle])
        prev = float(prev_ser.get(muscle, 0))
        bar_w = max(1, int(curr / max_vol * 18))
        bar = "█" * bar_w + "░" * (18 - bar_w)
        if prev > 0:
            pct = (curr - prev) / prev * 100
            color = "green" if pct >= 0 else "red"
            delta = f" [{color}]{'+' if pct >= 0 else ''}{pct:.0f}%[/{color}]"
        else:
            delta = " [dim]new[/dim]"
        console.print(f"    {muscle:<14} [cyan]{bar}[/cyan] {_fmt_weight(curr):>12}{delta}")


def _render_sync_report(counts: dict, is_full: bool) -> None:
    updated_ids: list[str] = counts.get("updated_ids", [])

    if is_full:
        console.print(
            Panel(
                f"[bold green]{counts.get('workouts', 0)}[/bold green] workouts  ·  "
                f"[bold]{counts.get('templates', 0)}[/bold] exercise templates  ·  "
                f"[bold]{counts.get('body_measurements', 0)}[/bold] body measurements",
                title=_("sync.full_complete_title"),
                border_style="green",
            )
        )
    else:
        updated = counts.get("updated", 0)
        deleted = counts.get("deleted", 0)
        if updated == 0 and deleted == 0:
            console.print(Panel(_("sync.already_up_to_date"), border_style="green"))
            _render_goals_progress()
            return
        parts = []
        if updated:
            parts.append(_("sync.n_new_updated", count=updated))
        if deleted:
            parts.append(_("sync.n_deleted", count=deleted))
        console.print(Panel(" · ".join(parts), title=_("sync.complete_title"), border_style="green"))

    if updated_ids:
        _render_workout_cards(updated_ids[:4])
        if len(updated_ids) > 4:
            console.print(_("sync.n_more", count=len(updated_ids) - 4))

    freq = workout_frequency(4)
    streak = freq.get("longest_streak_days", 0)
    if streak >= 2:
        fires = "🔥" * min(streak, 5)
        console.print(
            f"\n  {fires}  [bold]{streak}-day streak![/bold]  [dim]({freq['total_workouts']} sessions in last 4w)[/dim]"
        )

    _render_volume_delta()
    console.print()
    _render_goals_progress()


# ── header ────────────────────────────────────────────────────────────────────


def _show_header() -> None:
    from db.store import get_sync_state

    last_sync = get_sync_state("last_sync")
    total = (query("SELECT COUNT(*) as n FROM workouts") or [{"n": 0}])[0]["n"]
    week_count = (
        query("SELECT COUNT(*) as n FROM workouts WHERE start_time >= datetime('now', '-7 days')") or [{"n": 0}]
    )[0]["n"]
    freq = workout_frequency(4)
    goals = get_goals()
    name = get_pref("display_name")

    # Last workout
    lw_row = query("SELECT MAX(start_time) as t FROM workouts")
    lw_str = (
        _("header.last_workout", ago=_time_ago(lw_row[0]["t"]))
        if lw_row and lw_row[0]["t"]
        else _("header.no_workouts")
    )

    # Streak
    streak = freq.get("longest_streak_days", 0)
    streak_parts = []
    if streak >= 2:
        fires = "🔥" * min(streak, 5)
        streak_parts.append(_("header.streak", fires=fires, days=streak))

    # Routines count
    routine_count = (query("SELECT COUNT(*) as n FROM routines") or [{"n": 0}])[0]["n"]
    routines_str = _("header.routines_plural" if routine_count != 1 else "header.routines", count=routine_count)

    # Sync status
    if last_sync:
        try:
            secs = int((datetime.now(UTC) - datetime.fromisoformat(last_sync.replace("Z", "+00:00"))).total_seconds())
            sync_str = (
                _("header.sync_ok", ago=_time_ago(last_sync))
                if secs < _stale_seconds()
                else _("header.sync_stale", ago=_time_ago(last_sync))
            )
        except Exception:
            sync_str = _("header.sync_unknown")
    else:
        sync_str = _("header.sync_never")

    # AI provider
    from ai.provider import provider_label

    ai_str = _("header.ai", label=provider_label())

    # Recovery from Google Fit
    recovery_str = ""
    try:
        from fit.auth import is_connected

        if is_connected():
            from fit.analytics import recovery_score

            rec = recovery_score(3)
            if rec:
                recovery_str = _("header.recovery", color=rec["color"], score=rec["score"])
    except Exception:
        pass

    # Build lines
    line1_parts = [lw_str, *streak_parts, routines_str]
    line1 = "  ·  ".join(line1_parts)

    line2 = (
        f"[bold]{total}[/bold] workouts  ·  "
        f"[bold]{week_count}[/bold] this week  ·  "
        f"[bold]{freq['avg_per_week']}[/bold]/wk avg"
    )
    if goals:
        goals_str = _("header.goals_plural" if len(goals) != 1 else "header.goals", count=len(goals))
        line2 += f"  ·  {goals_str}"

    line3 = f"[dim]{ai_str}  ·  {sync_str}{recovery_str}[/dim]"

    brand = f"LIFTER [dim]v{_app_version()}[/dim]"
    title = f"[bold cyan]{brand}  [dim]·[/dim]  {_esc(name)}[/bold cyan]" if name else f"[bold cyan]{brand}[/bold cyan]"
    console.print(
        Panel(
            f"{line1}\n{line2}\n{line3}",
            title=title,
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()


# ── menu actions ──────────────────────────────────────────────────────────────


def _do_sync():
    client = _require_hevy()
    if not client:
        return

    sync_type = questionary.select(
        _("sync.type_prompt"),
        choices=[
            questionary.Choice(_("sync.incremental"), value="inc"),
            questionary.Choice(_("sync.full"), value="full"),
        ],
        style=STYLE,
    ).ask()
    if not sync_type:
        return

    _dlog("SYNC", "Manual sync started", type=sync_type)
    console.print()
    is_full = sync_type == "full"
    try:
        counts = full_sync(client) if is_full else incremental_sync(client)
    except RuntimeError as e:
        import debug_log

        debug_log.error("SYNC", "Manual sync failed", exc=e, type=sync_type)
        console.print(f"[red]{_esc(str(e))}[/red]")
        return
    _render_sync_report(counts, is_full)


def _do_stats():
    default_period = get_pref("default_stats_weeks") or "8 weeks"
    weeks_str = questionary.select(
        _("stats.time_period"),
        choices=_week_choices([4, 8, 12, 24]),
        default=default_period,
        style=STYLE,
    ).ask()
    if not weeks_str:
        return
    weeks = int(weeks_str.split()[0])
    _dlog("MENU", "Stats viewed", weeks=weeks)

    freq = workout_frequency(weeks)
    if freq["total_workouts"] == 0:
        console.print(_("error.no_data_sync_first"))
        return

    console.rule(_("stats.rule_title", weeks=weeks))

    t = Table(box=box.SIMPLE)
    t.add_column(_("stats.col_metric"), style="bold")
    t.add_column(_("stats.col_value"), justify="right")
    t.add_row(_("stats.total_workouts"), str(freq["total_workouts"]))
    t.add_row(_("stats.avg_per_week"), str(freq["avg_per_week"]))
    t.add_row(_("stats.avg_duration"), _("stats.minutes", n=freq["avg_duration_minutes"]))
    t.add_row(_("stats.avg_rest"), _("stats.days", n=freq["rest_day_avg"]))
    t.add_row(_("stats.longest_streak"), _("stats.days", n=freq["longest_streak_days"]))
    console.print(t)

    console.rule(_("stats.volume_rule"))
    muscle_vol = muscle_group_summary(weeks)
    sets_wk = sets_per_muscle_per_week(weeks)
    muscle_freq_data = muscle_group_frequency(weeks)
    max_vol = max(muscle_vol.values()) if muscle_vol else 1.0

    t2 = Table(box=box.SIMPLE)
    t2.add_column(_("stats.col_muscle"), style="bold")
    t2.add_column(_("stats.col_volume"), no_wrap=True)
    t2.add_column(_("stats.col_tonnage"), justify="right")
    t2.add_column(_("stats.col_sets_wk"), justify="right")
    t2.add_column(_("stats.col_sessions_wk"), justify="right")
    for muscle, vol in muscle_vol.items():
        bar_w = max(1, int(vol / max_vol * 16))
        bar = "█" * bar_w + "░" * (16 - bar_w)
        t2.add_row(
            muscle,
            f"[cyan]{bar}[/cyan]",
            _fmt_weight(vol),
            str(sets_wk.get(muscle, 0)),
            str(muscle_freq_data.get(muscle, 0)),
        )
    console.print(t2)

    body = body_measurement_trend(weeks)
    if body:
        console.rule(_("stats.body_rule"))
        bt = Table(box=box.SIMPLE)
        bt.add_column(_("stats.col_metric"), style="bold")
        bt.add_column(_("stats.col_latest"), justify="right")
        bt.add_column(_("stats.col_change", weeks=weeks), justify="right")
        wt_change = body.get("weight_change_kg")
        bt.add_row(
            _("stats.row_weight"),
            _fmt_weight(body.get("weight_kg")),
            _fmt_weight(wt_change) if wt_change not in (None, "—") else "—",
        )
        fat = body.get("fat_percent")
        fat_chg = body.get("fat_change_pct")
        bt.add_row(
            _("stats.row_body_fat"),
            f"{fat}%" if fat is not None else "—",
            f"{fat_chg}%" if fat_chg is not None else "—",
        )
        from analytics.records import compute_bmi
        from db.goals import get_height_cm

        bmi = compute_bmi(body.get("weight_kg"), get_height_cm())
        if bmi is not None:
            bt.add_row(_("stats.row_bmi"), str(bmi), "—")
        console.print(bt)

    prs = recent_prs(30)
    if prs:
        console.rule(_("stats.prs_rule"))
        pt = Table(box=box.SIMPLE)
        pt.add_column(_("stats.col_exercise"), style="bold")
        pt.add_column(_("stats.col_weight"), justify="right")
        pt.add_column(_("stats.col_reps"), justify="right")
        pt.add_column(_("stats.col_e1rm"), justify="right")
        pt.add_column(_("stats.col_date"))
        for pr in prs:
            pt.add_row(
                pr["exercise"], _fmt_weight(pr["weight_kg"]), str(pr["reps"]), _fmt_weight(pr["e1rm"]), pr["date"]
            )
        console.print(pt)

    plateaus = detect_plateaus(weeks)
    if plateaus:
        console.rule(_("stats.plateaus_rule"))
        for p in plateaus:
            console.print(
                f"  [yellow]•[/yellow] {p['exercise']} — stalled {p['sessions_stalled']} sessions (e1RM {p['current_e1rm']} kg)"
            )

    console.rule(_("stats.goals_rule"))
    _render_goals_progress()


# ── body measurements (manual entry / onboarding) ─────────────────────────────


def _is_number(v: str) -> bool:
    return v.strip().replace(".", "", 1).isdigit()


def _save_body_today(weight_kg: float | None = None, fat_percent: float | None = None) -> None:
    """Upsert today's body_measurements row, preserving other fields already set today."""
    from db.goals import _invalidate_render_cache
    from db.store import upsert_body_measurement

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
    from analytics.records import compute_bmi
    from db.goals import get_height_cm

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
    from db.goals import get_height_cm

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


def _do_progress():
    choice = questionary.select(
        _("progress.show_prompt"),
        choices=[
            questionary.Choice(_("progress.top_gainers"), value="top"),
            questionary.Choice(_("progress.specific_exercise"), value="exercise"),
        ],
        style=STYLE,
    ).ask()
    if not choice:
        return

    weeks_str = questionary.select(
        _("progress.time_period"),
        choices=_week_choices([8, 12, 24, 52]),
        default="12 weeks",
        style=STYLE,
    ).ask()
    if not weeks_str:
        return
    weeks = int(weeks_str.split()[0])
    _dlog("MENU", "Progression viewed", type=choice, weeks=weeks)

    if choice == "top":
        console.rule(_("progress.rule_top", weeks=weeks))
        top = top_progressions(weeks, top_n=10)
        if not top:
            console.print(_("error.not_enough_data"))
            return
        t = Table(box=box.SIMPLE)
        t.add_column(_("progress.col_exercise"), style="bold")
        t.add_column(_("progress.col_improvement"), justify="right")
        t.add_column(_("progress.col_start_e1rm"), justify="right")
        t.add_column(_("progress.col_current_e1rm"), justify="right")
        for g in top:
            t.add_row(
                g["exercise"], f"+{g['improvement_pct']}%", _fmt_weight(g["start_e1rm"]), _fmt_weight(g["current_e1rm"])
            )
        console.print(t)
    else:
        exercises = query("SELECT DISTINCT title FROM exercise_templates ORDER BY title")
        names = [e["title"] for e in exercises]
        if not names:
            console.print(_("error.no_exercises_sync_first"))
            return
        name = questionary.autocomplete(
            _("progress.search_prompt"),
            choices=names,
            style=STYLE,
            validate=lambda v: v in names or _("validate.pick_from_list"),
        ).ask()
        if not name:
            return
        rows = query("SELECT id FROM exercise_templates WHERE title = ?", (name,))
        if not rows:
            return
        console.rule(f"[bold]{name}[/bold]")
        df = exercise_progression(rows[0]["id"], weeks)
        if df.empty:
            console.print(_("error.no_progression_data"))
            return
        t = Table(box=box.SIMPLE)
        t.add_column(_("progress.col_date"))
        t.add_column(_("progress.col_best_weight"), justify="right")
        t.add_column(_("progress.col_reps"), justify="right")
        t.add_column(_("progress.col_e1rm"), justify="right")
        prev_e1rm = None
        for _idx, row in df.iterrows():
            change = ""
            if prev_e1rm is not None:
                delta = row["e1rm"] - prev_e1rm
                if delta > 0:
                    change = f" [green]+{_fmt_weight(delta)}[/green]"
                elif delta < 0:
                    change = f" [red]{_fmt_weight(delta)}[/red]"
            t.add_row(
                str(row["date"]),
                _fmt_weight(row["best_weight_kg"]),
                str(row["best_reps"]),
                f"{_fmt_weight(row['e1rm'])}{change}",
            )
            prev_e1rm = row["e1rm"]
        console.print(t)


def _do_records():
    _dlog("MENU", "Personal records viewed")
    prs = all_time_records()
    if not prs:
        console.print(_("error.no_records_sync_first"))
        return
    console.rule(_("records.rule_title"))
    t = Table(box=box.SIMPLE)
    t.add_column(_("stats.col_exercise"), style="bold")
    t.add_column(_("stats.col_weight"), justify="right")
    t.add_column(_("stats.col_reps"), justify="right")
    t.add_column(_("stats.col_e1rm"), justify="right")
    t.add_column(_("stats.col_date"))
    for pr in prs:
        t.add_row(pr["exercise"], _fmt_weight(pr["weight_kg"]), str(pr["reps"]), _fmt_weight(pr["e1rm"]), pr["date"])
    console.print(t)


def _do_goals():
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


def _do_coach():
    if not _require_ai():
        return
    weeks_str = questionary.select(
        _("coach.weeks_prompt"),
        choices=_week_choices([4, 8, 12, 16]),
        default=f"{_report_weeks()} weeks",
        style=STYLE,
    ).ask()
    if not weeks_str:
        return
    weeks = int(weeks_str.split()[0])

    generate_routine = questionary.confirm(
        _("coach.include_routine_prompt"),
        default=False,
        style=STYLE,
    ).ask()
    if generate_routine is None:
        return

    _run_report(weeks, generate_routine=generate_routine)


def _run_report(weeks: int, generate_routine: bool = False) -> bool:
    """Generate and render a coaching report for `weeks`. Returns True on success.

    `generate_routine` decides whether the AI also proposes a NEW routine — always an
    explicit choice, never automatic. The athlete's existing routines stay in the
    analysis context by default regardless (configurable in Settings → AI).
    """
    from ai.coach import get_coaching, push_routine_to_hevy

    _dlog("AI", "Coaching report requested", weeks=weeks, generate_routine=generate_routine)
    console.rule(_("coach.rule_title"))
    try:
        result = get_coaching(weeks=weeks, generate_routine=generate_routine)
    except Exception as e:
        from ai.coach import _friendly_error

        _dlog("ERROR", f"Coaching report failed: {type(e).__name__}", error=str(e)[:200])
        console.print(f"[red]{_friendly_error(e)}[/red]")
        return False

    # ── scores ────────────────────────────────────────────────────────────────
    ws = result.get("workout_score")
    hs = result.get("health_score")
    cs = result.get("combined_score")
    score_items = [(_("score.training"), ws), (_("score.health"), hs), (_("score.overall"), cs)]
    score_lines = [_fmt_score_bar(lbl, int(val), bar_width=16) for lbl, val in score_items if val is not None]
    if score_lines:
        console.print(
            Panel(
                "\n".join(score_lines),
                title=_("coach.scores_panel_title"),
                border_style="cyan",
                padding=(0, 2),
            )
        )
        # Cache for snapshot panel
        if ws is not None:
            set_pref("last_workout_score", str(int(ws)))
        if hs is not None:
            set_pref("last_health_score", str(int(hs)))
        if cs is not None:
            set_pref("last_combined_score", str(int(cs)))

    # ── muscle / group distribution ───────────────────────────────────────────
    spw = sets_per_muscle_per_week(weeks)
    groups = _sets_by_group(weeks)
    if spw or groups:
        dist_lines = []

        BAR_G, BAR_M = 20, 18
        if groups:
            total_g = sum(groups.values()) or 1
            max_g = max(groups.values())
            dist_lines.append("[bold]By muscle group[/bold]")
            for grp, s in sorted(groups.items(), key=lambda x: -x[1]):
                pct = s / total_g * 100
                bw = max(1, int(s / max_g * 0.7 * BAR_G))
                color = _score_color(int(min(pct * 2, 100)))
                bar = f"[{color}]{'█' * bw}[/{color}][dim]{'░' * (BAR_G - bw)}[/dim]"
                dist_lines.append(f"  {grp:<12} {bar}  {pct:.0f}%  [dim]({s:.0f} sets/wk avg)[/dim]")
            dist_lines.append("")

        if spw:
            total_m = sum(spw.values()) or 1
            max_m = max(spw.values())
            dist_lines.append("[bold]By muscle[/bold]")
            for muscle, s in sorted(spw.items(), key=lambda x: -x[1]):
                pct = s / total_m * 100
                bw = max(1, int(s / max_m * 0.7 * BAR_M))
                bar = f"[cyan]{'█' * bw}[/cyan][dim]{'░' * (BAR_M - bw)}[/dim]"
                dist_lines.append(f"  {muscle:<16} {bar}  {pct:.0f}%")

        if dist_lines:
            console.print(
                Panel(
                    "\n".join(dist_lines),
                    title=_("coach.volume_dist_title"),
                    border_style="cyan",
                    padding=(0, 2),
                )
            )

    # ── analysis ──────────────────────────────────────────────────────────────
    console.rule(_("coach.strengths_rule"))
    for s in result.get("strengths", []):
        console.print(f"  [green]✓[/green] {s}")

    console.rule(_("coach.weaknesses_rule"))
    for w in result.get("weaknesses", []):
        console.print(f"  [yellow]![/yellow] {w}")

    console.rule(_("coach.recommendations_rule"))
    for r in result.get("recommendations", []):
        console.print(f"  [cyan]→[/cyan] {r}")

    console.rule(_("coach.next_focus_rule"))
    console.print(f"  {result.get('next_focus', '')}")

    # ── suggested routine ─────────────────────────────────────────────────────
    routine = result.get("routine", {})
    if routine:
        from ai.routine_schema import validate_routine_args

        routine, _val_errors = validate_routine_args(routine)
        if routine is None:
            import debug_log

            debug_log.error("AI", "Report routine rejected by validation", errors="; ".join(_val_errors)[:300])
            console.print(_("chat.routine_invalid"))
    if routine:
        console.rule(_("coach.suggested_routine_rule", title=routine.get("title")))
        console.print(f"  [dim]{routine.get('notes')}[/dim]\n")
        for ex in routine.get("exercises", []):
            if not isinstance(ex, dict):
                continue

            def _fmt_set(s: dict) -> str:
                w = s.get("weight_kg")
                w_str = _fmt_weight(w) if w else "BW"
                return f"[dim]{s.get('type', 'normal')}[/dim] {w_str}×{s.get('reps', '?')}"

            sets_str = "  ".join(_fmt_set(s) for s in ex.get("sets", []) if isinstance(s, dict))
            ex_title = ex.get("title") or ex.get("exercise_template_id", "Exercise")
            console.print(f"  [bold]{ex_title}[/bold]")
            console.print(f"    {sets_str}")
            if ex.get("notes"):
                console.print(f"    [dim italic]{ex['notes']}[/dim italic]")
        console.print()
        n_exercises = len([e for e in routine.get("exercises", []) if isinstance(e, dict)])
        if questionary.confirm(
            _("coach.push_routine_prompt", title=routine.get("title"), count=n_exercises),
            default=False,
            style=STYLE,
        ).ask():
            client = _require_hevy()
            if client:
                try:
                    from ai.coach import _show_exercise_benefits
                    from hevy.client import _routine_id

                    resp = push_routine_to_hevy(routine)
                    console.print(_("coach.routine_pushed", routine_id=_routine_id(resp)))
                    _show_exercise_benefits(routine.get("exercises", []))
                except Exception as e:
                    console.print(f"[red]{e}[/red]")

    mark_report_generated()
    return True


def _do_chat():
    if not _require_ai():
        return  # None ⇒ main loop pauses, so the error stays visible
    week_choices = [
        questionary.Choice(_("time.weeks", n=4), value=4),
        questionary.Choice(_("time.weeks", n=8), value=8),
        questionary.Choice(_("time.weeks", n=12), value=12),
        questionary.Choice(_("chat.all_time"), value=16),
    ]
    weeks_str = questionary.select(
        _("chat.context_prompt"),
        choices=week_choices,
        default=week_choices[1],
        style=STYLE,
    ).ask()
    if not weeks_str:
        return NO_PAUSE
    weeks = int(weeks_str)
    _dlog("AI", "Chat requested", weeks=weeks)
    from ai.coach import start_enhanced_chat

    start_enhanced_chat(weeks=weeks)
    return NO_PAUSE


# ── settings & reset ─────────────────────────────────────────────────────────

_AI_LANGUAGES = [
    "English",
    "Portuguese (BR)",
    "Portuguese (PT)",
    "Spanish",
    "French",
    "German",
    "Italian",
    "Dutch",
    "Polish",
    "Russian",
    "Japanese",
    "Chinese",
]

_UI_LANGUAGES = [
    ("en", "English"),
    ("pt_BR", "Português (Brasil)"),
]


# (env var, i18n label key, hidden input)
_ENV_KEY_FIELDS: list[tuple[str, str, bool]] = [
    ("GEMINI_API_KEY", "settings.keys.gemini", True),
    ("ANTHROPIC_API_KEY", "settings.keys.claude", True),
    ("OPENROUTER_API_KEY", "settings.keys.openrouter", True),
    ("GROQ_API_KEY", "settings.keys.groq", True),
    ("GITHUB_TOKEN", "settings.keys.github", True),
    ("AWS_BEARER_TOKEN_BEDROCK", "settings.keys.bedrock_token", True),
    ("AWS_REGION", "settings.keys.aws_region", False),
    ("AWS_ACCESS_KEY_ID", "settings.keys.aws_access", True),
    ("AWS_SECRET_ACCESS_KEY", "settings.keys.aws_secret", True),
    ("AWS_SESSION_TOKEN", "settings.keys.aws_session", True),
]


def _mask_secret(value: str) -> str:
    if not value or _is_placeholder_key(value):
        return _("settings.keys.not_set")
    return f"…{value[-4:]}" if len(value) > 4 else "…"


def _do_api_keys_settings() -> None:
    """Edit provider credentials in-app — persisted to the global .env file."""
    import paths

    while True:
        console.clear()
        lines = [_("settings.keys.env_file_line", path=_esc(str(paths.ENV_FILE))), ""]
        for var, label_key, _hidden in _ENV_KEY_FIELDS:
            lines.append(f"{_(label_key)}: [bold]{_esc(_mask_secret(getattr(config, var, '')))}[/bold]")
        console.print(Panel("\n".join(lines), title=_("settings.keys.title"), border_style="cyan", padding=(0, 2)))

        choices = [
            questionary.Choice(f"  {_(label_key)}  ({_mask_secret(getattr(config, var, ''))})", value=var)
            for var, label_key, _hidden in _ENV_KEY_FIELDS
        ]
        choices += [questionary.Separator("  ───"), questionary.Choice(_("nav.back"), value="back")]
        picked = questionary.select(_("settings.keys.prompt"), choices=choices, style=STYLE).ask()
        if not picked or picked == "back":
            return

        hidden = next(h for var, _k, h in _ENV_KEY_FIELDS if var == picked)
        action = questionary.select(
            _("settings.keys.action_prompt", field=picked, current=_mask_secret(getattr(config, picked, ""))),
            choices=[
                questionary.Choice(_("settings.keys.set_option"), value="set"),
                questionary.Choice(_("settings.keys.clear_option"), value="clear"),
                questionary.Choice(_("nav.cancel"), value=None),
            ],
            style=STYLE,
        ).ask()
        if not action:
            continue

        if action == "set":
            asker = questionary.password if hidden else questionary.text
            value = asker(_("settings.keys.value_prompt"), style=STYLE).ask()
            if not value or not value.strip():
                continue
            new_value = value.strip()
        else:
            new_value = ""  # written as KEY= so reload_env(override=True) clears it

        config.set_env_values({picked: new_value})
        config.reload_env()
        config.apply_ai_overrides()  # profile prefs keep winning over fresh env
        _dlog("SETTING", "env key updated", var=picked, cleared=not new_value)
        if new_value:
            console.print(_("settings.keys.saved", var=picked))
        else:
            console.print(_("settings.keys.cleared", var=picked))


def _do_ai_settings():
    from db.goals import (
        get_pref,
        get_token_budget,
        get_token_reset_day,
        get_token_usage,
        get_token_usage_month,
        reset_token_usage,
        set_pref,
        set_token_budget,
        set_token_reset_day,
        token_budget_status,
    )
    from db.memories import count_memories

    def _token_block(title, usage):
        total = usage["input"] + usage["output"]
        cache_pct = int(usage["cache_read"] / usage["input"] * 100) if usage["input"] else 0
        block = [
            title,
            _("settings.ai.tokens_input", count=f"{usage['input']:,}"),
            _("settings.ai.tokens_output", count=f"{usage['output']:,}"),
            _("settings.ai.tokens_total", count=f"{total:,}"),
        ]
        if usage["cache_read"]:
            block.append(_("settings.ai.tokens_cached", count=f"{usage['cache_read']:,}", pct=cache_pct))
        return block

    while True:
        console.clear()
        usage_month = get_token_usage_month()
        usage_total = get_token_usage()
        reset_day = get_token_reset_day()
        try:
            history_turns = max(0, int(get_pref("ai_chat_history_turns") or 12))
        except (TypeError, ValueError):
            history_turns = 12
        history_label = (
            _("settings.ai.history_unlimited")
            if history_turns == 0
            else _("settings.ai.history_count", n=history_turns)
        )
        slim_on = get_pref("ai_chat_slim") != "0"
        routines_on = get_pref("ai_include_routines") != "0"
        auto_report_on = get_pref("auto_report") != "0"
        send_name_on = get_pref("ai_send_name") != "0"
        send_body_on = get_pref("ai_send_body") != "0"
        report_weeks = _report_weeks()
        budget_status = token_budget_status()
        mem_count = count_memories()
        lang = get_pref("ai_language") or "English"
        if lang == "Portuguese":
            lang = "Portuguese (BR)"
            set_pref("ai_language", lang)

        slim_label = _("settings.ai.context_slim") if slim_on else _("settings.ai.context_full")

        def on_off(b):
            return _("settings.ai.on") if b else _("settings.ai.off")

        budget_label = (
            _("settings.ai.budget_off")
            if budget_status is None
            else _("settings.ai.budget_usage", budget=f"{budget_status['budget']:,}", pct=int(budget_status["pct"]))
        )

        lines = [
            _("settings.ai.provider_line", provider=config.AI_PROVIDER, model=config.AI_MODEL),
            _("settings.ai.context_line", mode=slim_label),
            _("settings.ai.routines_line", state=on_off(routines_on)),
            _("settings.ai.auto_report_line", state=on_off(auto_report_on)),
            _("settings.ai.report_weeks_line", weeks=report_weeks),
            _("settings.ai.privacy_name_line", state=on_off(send_name_on)),
            _("settings.ai.privacy_body_line", state=on_off(send_body_on)),
            _("settings.ai.language_line", lang=lang),
            _("settings.ai.history_turns_line", turns=history_label),
            _("settings.ai.reset_day_line", day=reset_day),
            _("settings.ai.budget_line", budget=budget_label),
        ]
        if budget_status and budget_status["pct"] >= 100:
            lines.append(
                _(
                    "settings.ai.budget_exceeded",
                    used=f"{budget_status['used']:,}",
                    budget=f"{budget_status['budget']:,}",
                )
            )
        elif budget_status and budget_status["pct"] >= 80:
            lines.append(
                _(
                    "settings.ai.budget_warning",
                    pct=int(budget_status["pct"]),
                    used=f"{budget_status['used']:,}",
                    budget=f"{budget_status['budget']:,}",
                )
            )
        lines.append("")
        lines += _token_block(_("settings.ai.token_usage_month_title"), usage_month)
        lines.append("")
        lines += _token_block(_("settings.ai.token_usage_total_title"), usage_total)

        console.print(Panel("\n".join(lines), title=_("settings.ai.title"), border_style="cyan"))

        action = questionary.select(
            _("settings.ai.prompt"),
            choices=[
                questionary.Choice(_("settings.ai.provider_choice", provider=config.AI_PROVIDER), value="provider"),
                questionary.Choice(_("settings.ai.model_choice", model=config.AI_MODEL), value="model"),
                questionary.Choice(_("settings.ai.api_keys_choice"), value="api_keys"),
                questionary.Choice(
                    _("settings.ai.toggle_context_choice", mode="Slim" if slim_on else "Full"),
                    value="toggle_slim",
                ),
                questionary.Choice(
                    _("settings.ai.toggle_routines_choice", state=on_off(routines_on)),
                    value="toggle_routines",
                ),
                questionary.Choice(
                    _("settings.ai.toggle_auto_report_choice", state=on_off(auto_report_on)),
                    value="toggle_auto_report",
                ),
                questionary.Choice(_("settings.ai.report_weeks_choice", weeks=report_weeks), value="report_weeks"),
                questionary.Choice(
                    _("settings.ai.toggle_name_choice", state=on_off(send_name_on)), value="toggle_name"
                ),
                questionary.Choice(
                    _("settings.ai.toggle_body_choice", state=on_off(send_body_on)), value="toggle_body"
                ),
                questionary.Choice(_("settings.ai.language_choice", lang=lang), value="language"),
                questionary.Choice(_("settings.ai.history_turns_choice", turns=history_label), value="history_turns"),
                questionary.Choice(_("settings.ai.memories_choice", count=mem_count), value="memories"),
                questionary.Choice(_("settings.ai.budget_choice", budget=budget_label), value="budget"),
                questionary.Choice(_("settings.ai.reset_day_choice", day=reset_day), value="reset_day"),
                questionary.Choice(_("settings.ai.reset_tokens_choice"), value="reset_tokens"),
                questionary.Separator("  ───"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "provider":
            configured = [p for p in sorted(config.KNOWN_PROVIDERS) if _provider_key_ok(p)]
            if not configured:
                console.print(_("settings.ai.provider_none"))
                continue
            choices = [questionary.Choice(f"{p}  ·  {config.default_model_for(p)}", value=p) for p in configured]
            choices.append(
                questionary.Choice(_("settings.ai.provider_env_option", provider=config._ENV_AI_PROVIDER), value="_env")
            )
            choices.append(questionary.Choice(_("nav.cancel"), value=None))
            picked = questionary.select(_("settings.ai.provider_prompt"), choices=choices, style=STYLE).ask()
            if picked == "_env":
                set_pref("ai_provider", "")
                set_pref("ai_model", "")
            elif picked and picked != config.AI_PROVIDER:
                set_pref("ai_provider", picked)
                set_pref("ai_model", "")  # model names are provider-specific
            if picked:
                config.apply_ai_overrides()
                _dlog("SETTING", "ai_provider changed", provider=config.AI_PROVIDER, model=config.AI_MODEL)
                console.print(_("settings.ai.provider_saved", provider=config.AI_PROVIDER, model=config.AI_MODEL))
            continue

        if action == "model":
            default_model = config.default_model_for(config.AI_PROVIDER)
            picked = questionary.select(
                _("settings.ai.model_prompt", provider=config.AI_PROVIDER),
                choices=[
                    questionary.Choice(_("settings.ai.model_default_option", model=default_model), value=default_model),
                    questionary.Choice(_("settings.ai.model_custom_option"), value="_custom"),
                    questionary.Choice(_("nav.cancel"), value=None),
                ],
                style=STYLE,
            ).ask()
            if picked == "_custom":
                picked = questionary.text(
                    _("settings.ai.model_custom_prompt"),
                    default=config.AI_MODEL,
                    validate=lambda v: bool(v.strip()) or _("settings.ai.model_invalid"),
                    style=STYLE,
                ).ask()
                picked = picked.strip() if picked else None
            if picked:
                set_pref("ai_model", picked)
                config.apply_ai_overrides()
                _dlog("SETTING", "ai_model changed", model=config.AI_MODEL)
                console.print(_("settings.ai.model_saved", model=config.AI_MODEL))
            continue

        if action == "api_keys":
            _do_api_keys_settings()
            continue

        if action == "toggle_slim":
            new_val = "0" if slim_on else "1"
            set_pref("ai_chat_slim", new_val)
            label = _("settings.ai.context_slim") if new_val == "1" else _("settings.ai.context_full")
            _dlog("SETTING", "ai_chat_slim changed", value=label)
            console.print(_("settings.ai.context_saved", mode=label))

        elif action == "toggle_routines":
            new_val = "0" if routines_on else "1"
            set_pref("ai_include_routines", new_val)
            _dlog("SETTING", "ai_include_routines changed", value=new_val)
            console.print(_("settings.ai.routines_saved", state=on_off(new_val != "0")))

        elif action == "toggle_auto_report":
            new_val = "0" if auto_report_on else "1"
            set_pref("auto_report", new_val)
            _dlog("SETTING", "auto_report changed", value=new_val)
            console.print(_("settings.ai.auto_report_saved", state=on_off(new_val != "0")))

        elif action == "report_weeks":
            answer = questionary.select(
                _("settings.ai.report_weeks_prompt"),
                choices=_week_choices([4, 8, 12]),
                default=f"{report_weeks} weeks",
                style=STYLE,
            ).ask()
            if answer:
                new_weeks = int(answer.split()[0])
                set_pref("report_weeks", str(new_weeks))
                _dlog("SETTING", "report_weeks changed", value=new_weeks)
                console.print(_("settings.ai.report_weeks_saved", weeks=new_weeks))

        elif action == "toggle_name":
            new_val = "0" if send_name_on else "1"
            set_pref("ai_send_name", new_val)
            _dlog("SETTING", "ai_send_name changed", value=new_val)
            console.print(_("settings.ai.privacy_name_saved", state=on_off(new_val != "0")))

        elif action == "toggle_body":
            new_val = "0" if send_body_on else "1"
            set_pref("ai_send_body", new_val)
            _dlog("SETTING", "ai_send_body changed", value=new_val)
            console.print(_("settings.ai.privacy_body_saved", state=on_off(new_val != "0")))

        elif action == "memories":
            _do_manage_memories()

        elif action == "budget":
            answer = questionary.text(
                _("settings.ai.budget_prompt"),
                default=str(get_token_budget()),
                validate=lambda v: v.isdigit() or _("settings.ai.budget_invalid"),
                style=STYLE,
            ).ask()
            if answer is not None and answer.isdigit():
                set_token_budget(int(answer))
                _dlog("SETTING", "ai_tokens_month_budget changed", value=int(answer))
                console.print(_("settings.ai.budget_saved", budget=f"{int(answer):,}"))

        elif action == "language":
            lang_choices = _AI_LANGUAGES + ([] if lang in _AI_LANGUAGES else [lang])
            new_lang = questionary.select(
                _("settings.ai.language_prompt"),
                choices=lang_choices,
                default=lang if lang in lang_choices else lang_choices[0],
                style=STYLE,
            ).ask()
            if new_lang:
                set_pref("ai_language", new_lang)
                _dlog("SETTING", "ai_language changed", value=new_lang)
                console.print(_("settings.ai.language_saved", lang=new_lang))

        elif action == "history_turns":
            answer = questionary.text(
                _("settings.ai.history_turns_prompt"),
                default=str(history_turns),
                validate=lambda v: (v.isdigit() and 0 <= int(v) <= 100) or _("settings.ai.history_turns_invalid"),
                style=STYLE,
            ).ask()
            if answer is not None and answer.isdigit():
                n = int(answer)
                set_pref("ai_chat_history_turns", str(n))
                _dlog("SETTING", "ai_chat_history_turns changed", value=n)
                saved_label = _("settings.ai.history_unlimited") if n == 0 else _("settings.ai.history_count", n=n)
                console.print(_("settings.ai.history_turns_saved", turns=saved_label))

        elif action == "reset_day":
            answer = questionary.text(
                _("settings.ai.reset_day_prompt"),
                default=str(reset_day),
                validate=lambda v: (v.isdigit() and 1 <= int(v) <= 31) or _("settings.ai.reset_day_invalid"),
                style=STYLE,
            ).ask()
            if answer:
                set_token_reset_day(int(answer))
                _dlog("SETTING", "ai_tokens_reset_day changed", value=answer)
                console.print(_("settings.ai.reset_day_saved", day=int(answer)))

        elif action == "reset_tokens":
            if questionary.confirm(_("settings.ai.reset_tokens_prompt"), default=False, style=STYLE).ask():
                reset_token_usage()
                _dlog("SETTING", "token counters reset (month + lifetime)")
                console.print(_("settings.ai.reset_tokens_done"))


def _do_manage_memories() -> None:
    from db.goals import get_pref, set_pref
    from db.memories import count_memories, delete_memories, enforce_memory_cap, get_all_memories

    while True:
        console.clear()
        try:
            mem_max = max(0, int(get_pref("memories_max") or 200))
        except (TypeError, ValueError):
            mem_max = 200
        max_label = _("settings.ai.memories_unlimited") if mem_max == 0 else str(mem_max)
        console.print(
            Panel(
                _("settings.ai.memories_panel", count=count_memories(), max=max_label),
                title=_("settings.ai.memories_title"),
                border_style="cyan",
                padding=(0, 2),
            )
        )

        action = questionary.select(
            _("settings.ai.memories_prompt"),
            choices=[
                questionary.Choice(_("settings.ai.memories_delete_choice"), value="delete"),
                questionary.Choice(_("settings.ai.memories_limit_choice", max=max_label), value="limit"),
                questionary.Separator("  ───"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "delete":
            memories = get_all_memories()
            if not memories:
                console.print(_("settings.ai.memories_none"))
                questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()
                continue
            choices = [
                questionary.Choice(f"[{(m['created_at'] or '')[:10]}] {m['summary'][:70]}", value=m["id"])
                for m in memories
            ]
            picked = questionary.checkbox(_("settings.ai.memories_select"), choices=choices, style=STYLE).ask()
            if not picked:
                continue
            if questionary.confirm(
                _("settings.ai.memories_delete_confirm", count=len(picked)), default=False, style=STYLE
            ).ask():
                deleted = delete_memories(picked)
                _dlog("SETTING", "memories deleted", count=deleted)
                console.print(_("settings.ai.memories_deleted", count=deleted))
                questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()

        elif action == "limit":
            answer = questionary.text(
                _("settings.ai.memories_limit_prompt"),
                default=str(mem_max),
                validate=lambda v: (v.isdigit() and 0 <= int(v) <= 10000) or _("settings.ai.memories_limit_invalid"),
                style=STYLE,
            ).ask()
            if answer is not None and answer.isdigit():
                new_max = int(answer)
                set_pref("memories_max", str(new_max))
                enforce_memory_cap()
                _dlog("SETTING", "memories_max changed", value=new_max)
                saved_label = _("settings.ai.memories_unlimited") if new_max == 0 else str(new_max)
                console.print(_("settings.ai.memories_limit_saved", max=saved_label))


def _do_data_reset():
    while True:
        console.clear()
        action = questionary.select(
            _("data_reset.prompt"),
            choices=[
                questionary.Choice(_("data_reset.memories_choice"), value="memories"),
                questionary.Choice(_("data_reset.goals_choice"), value="goals"),
                questionary.Choice(_("data_reset.sync_state_choice"), value="sync_state"),
                questionary.Choice(_("data_reset.all_choice"), value="all"),
                questionary.Separator("  ───"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "memories":
            if confirm_destructive(_("data_reset.memories_confirm"), double=True):
                from db.memories import clear_memories

                clear_memories()
                _dlog("RESET", "Coach memories cleared")
                console.print(_("data_reset.memories_done"))

        elif action == "goals":
            if confirm_destructive(_("data_reset.goals_confirm"), double=True):
                from db.goals import clear_goals

                clear_goals()
                _dlog("RESET", "All goals cleared")
                console.print(_("data_reset.goals_done"))

        elif action == "sync_state":
            if questionary.confirm(_("data_reset.sync_state_confirm"), default=False, style=STYLE).ask():
                from db.store import set_sync_state

                set_sync_state("last_sync", "1970-01-01T00:00:00Z")
                _dlog("RESET", "Sync state reset")
                console.print(_("data_reset.sync_state_done"))

        elif action == "all":
            # Spell out exactly what the wipe removes so it's never a surprise.
            def _count(sql):
                try:
                    return query(sql)[0]["n"]
                except Exception:
                    return 0

            summary_lines = [
                _("data_reset.all_item_workouts", n=_count("SELECT COUNT(*) AS n FROM workouts")),
                _("data_reset.all_item_goals", n=_count("SELECT COUNT(*) AS n FROM user_goals")),
                _("data_reset.all_item_measurements", n=_count("SELECT COUNT(*) AS n FROM body_measurements")),
                _("data_reset.all_item_routines", n=_count("SELECT COUNT(*) AS n FROM routines")),
                _("data_reset.all_item_memories", n=_count("SELECT COUNT(*) AS n FROM chat_memories")),
                _("data_reset.all_item_fit", n=_count("SELECT COUNT(*) AS n FROM fit_daily")),
            ]
            console.print(
                Panel(
                    "\n".join(summary_lines),
                    title=_("data_reset.all_summary_title"),
                    border_style="red",
                )
            )
            console.print(_("data_reset.all_warning"))
            if not questionary.confirm(_("data_reset.all_confirm1"), default=False, style=STYLE).ask():
                continue
            if not questionary.confirm(_("data_reset.all_confirm2"), default=False, style=STYLE).ask():
                continue

            import os

            from config import DB_PATH

            try:
                from fit.auth import disconnect as fit_disconnect

                fit_disconnect()
            except Exception:
                pass
            # Remove the WAL/SHM sidecars too — leftover WAL pages still hold
            # wiped data and could be replayed into the recreated database.
            for suffix in ("", "-wal", "-shm"):
                with contextlib.suppress(FileNotFoundError):
                    os.remove(f"{DB_PATH}{suffix}")
            init_db()  # callers up the menu chain read prefs immediately

            from render_cache import invalidate

            invalidate()
            _dlog("RESET", "Full data wipe executed")
            console.print(_("data_reset.all_done"))
            return  # DB is gone — exit all the way back to main


def _do_create_profile_flow() -> str:
    """Interactive profile creation. Returns the new slug."""
    from profile_mgr import PROFILES_DIR, create_profile

    name = (
        questionary.text(
            _("profiles.name_prompt"),
            validate=lambda v: bool(v.strip()) or _("validate.name_required"),
            style=STYLE,
        ).ask()
        or ""
    ).strip()
    if not name:
        name = "New Profile"
    api_key = (
        questionary.text(
            _("profiles.api_key_prompt"),
            style=STYLE,
        ).ask()
        or ""
    ).strip()
    lang_choices = [questionary.Choice(lname, value=code) for code, lname in _UI_LANGUAGES]
    lang_code = (
        questionary.select(
            _("profiles.language_prompt"),
            choices=lang_choices,
            style=STYLE,
        ).ask()
        or "en"
    )
    profile = create_profile(name, hevy_api_key=api_key)
    slug = profile["slug"]
    # Write the language pref into the new profile's DB now so it survives
    # a process restart when the user switches to this profile immediately.
    old_db = config.DB_PATH
    config.DB_PATH = PROFILES_DIR / slug / "hevy.db"
    try:
        init_db()
        set_pref("ui_language", lang_code)
    finally:
        config.DB_PATH = old_db
    console.print(_("profiles.created", name=_esc(name)))
    return slug


def _do_profiles_menu() -> None:
    from profile_mgr import (
        delete_profile,
        get_active_slug,
        get_profile_name,
        list_profiles,
        rename_profile,
        set_active_slug,
    )

    while True:
        console.clear()
        active_slug = get_active_slug()
        active_name = get_profile_name(active_slug) if active_slug else "None"
        profiles = list_profiles()

        console.print(
            Panel(
                _("profiles.panel_content", name=_esc(active_name), total=len(profiles)),
                title=_("profiles.panel_title"),
                border_style="cyan",
                padding=(0, 2),
            )
        )

        action = questionary.select(
            _("profiles.menu_prompt"),
            choices=[
                questionary.Choice(_("profiles.switch_choice"), value="switch"),
                questionary.Choice(_("profiles.create_choice"), value="create"),
                questionary.Choice(_("profiles.rename_choice"), value="rename"),
                questionary.Choice(_("profiles.delete_choice"), value="delete"),
                questionary.Separator("  ───────────────────────────────────────"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "switch":
            if len(profiles) <= 1:
                console.print(_("profiles.only_one"))
                questionary.press_any_key_to_continue(_("nav.press_any_key"), style=STYLE).ask()
                continue
            choices = [
                questionary.Choice(
                    f"  {p['name']}{_('profiles.active_suffix') if p['slug'] == active_slug else ''}",
                    value=p["slug"],
                )
                for p in profiles
            ]
            choices.append(questionary.Separator("  ──────────────────────────────────"))
            choices.append(questionary.Choice(_("nav.cancel"), value=None))
            slug = questionary.select(_("profiles.switch_prompt"), choices=choices, style=STYLE).ask()
            if slug and slug != active_slug:
                _dlog("PROFILE", "Profile switch requested", from_slug=active_slug, to_slug=slug)
                set_active_slug(slug)
                console.print(_("profiles.switching", name=_esc(get_profile_name(slug))))
                import os as _os
                import sys as _sys

                _os.execv(_sys.executable, [_sys.executable, *_sys.argv])

        elif action == "create":
            slug = _do_create_profile_flow()
            if questionary.confirm(_("profiles.switch_now"), default=True, style=STYLE).ask():
                _dlog("PROFILE", "Switched to newly created profile", slug=slug)
                set_active_slug(slug)
                import os as _os
                import sys as _sys

                _os.execv(_sys.executable, [_sys.executable, *_sys.argv])

        elif action == "rename":
            if active_slug:
                new_name = (
                    questionary.text(
                        _("profiles.rename_prompt", name=_esc(active_name)),
                        style=STYLE,
                    ).ask()
                    or ""
                ).strip()
                if new_name:
                    rename_profile(active_slug, new_name)
                    console.print(_("profiles.renamed", name=_esc(new_name)))

        elif action == "delete":
            others = [p for p in profiles if p["slug"] != active_slug]
            if not others:
                console.print(_("profiles.cannot_delete_only"))
                questionary.press_any_key_to_continue(_("nav.press_any_key"), style=STYLE).ask()
                continue
            choices = [questionary.Choice(f"  {p['name']}", value=p["slug"]) for p in others]
            choices.append(questionary.Separator("  ──────────────────────────────────"))
            choices.append(questionary.Choice(_("nav.cancel"), value=None))
            slug = questionary.select(_("profiles.delete_prompt"), choices=choices, style=STYLE).ask()
            if slug:
                pname = get_profile_name(slug)
                if questionary.confirm(
                    _("profiles.delete_confirm", name=_esc(pname)),
                    default=False,
                    style=STYLE,
                ).ask():
                    delete_profile(slug)
                    console.print(_("profiles.deleted", name=_esc(pname)))


def _do_profile_settings() -> None:
    import json as _json

    from profile_mgr import PROFILES_DIR, get_active_slug, update_profile_key

    active_slug = get_active_slug()
    name = get_pref("display_name") or ""

    hevy_key = ""
    if active_slug:
        cfg_file = PROFILES_DIR / active_slug / "profile.json"
        if cfg_file.exists():
            with contextlib.suppress(Exception):
                hevy_key = _json.loads(cfg_file.read_text()).get("hevy_api_key", "")
    masked_key = (hevy_key[:4] + "…" + hevy_key[-4:]) if len(hevy_key) > 8 else ("set" if hevy_key else "not set")

    from db.goals import get_height_cm

    height_cm = get_height_cm()
    height_line = _("profile.height_label", height=_fmt_height(height_cm)) if height_cm else _("profile.height_notset")

    name_line = _("profile.display_name_label", name=_esc(name)) if name else _("profile.display_name_notset")
    console.print(
        Panel(
            f"{name_line}\n{_('profile.api_key_label', key=masked_key)}\n{height_line}",
            title=_("profile.panel_title"),
            border_style="cyan",
            padding=(0, 2),
        )
    )

    action = questionary.select(
        _("profile.edit_prompt"),
        choices=[
            questionary.Choice(_("profile.display_name_choice"), value="name"),
            questionary.Choice(_("profile.api_key_choice"), value="apikey"),
            questionary.Choice(_("profile.height_choice"), value="height"),
            questionary.Choice(_("nav.cancel"), value="back"),
        ],
        style=STYLE,
    ).ask()

    if action == "name":
        new_name = (questionary.text(_("profile.new_name_prompt"), style=STYLE).ask() or "").strip()
        if new_name:
            set_pref("display_name", new_name)
            _dlog("SETTING", "display_name changed")
            console.print(_("profile.name_updated", name=_esc(new_name)))

    elif action == "apikey" and active_slug:
        new_key = (questionary.text(_("profile.new_api_key_prompt"), style=STYLE).ask() or "").strip()
        if new_key:
            update_profile_key(active_slug, new_key)
            config.HEVY_API_KEY = new_key
            _dlog("SETTING", "hevy_api_key updated", profile=active_slug)
            console.print(_("profile.api_key_updated"))

    elif action == "height":
        _prompt_and_save_height()


def _do_preferences_settings() -> None:
    import i18n as _i18n

    while True:
        console.clear()
        units = _get_units()
        checkin_days = int(get_pref("goals_checkin_days") or 7)
        auto_sync = get_pref("auto_sync") == "1"
        default_weeks = get_pref("default_stats_weeks") or "8 weeks"
        stale_hours = _stale_seconds() // 3600
        ui_lang_code = get_pref("ui_language") or config.DEFAULT_LANGUAGE
        ui_lang_name = dict(_UI_LANGUAGES).get(ui_lang_code, ui_lang_code)
        on_str = _("settings.on")
        off_str = _("settings.off")

        import debug_log

        lines = [
            _("settings.prefs.units_label", units=units),
            _("settings.prefs.checkin_label", days=checkin_days),
            _("settings.prefs.autosync_label", state=on_str if auto_sync else off_str),
            _("settings.prefs.stale_hours_label", hours=stale_hours),
            _("settings.prefs.stats_window_label", window=default_weeks),
            _("settings.prefs.ui_language_label", lang=ui_lang_name),
            _("settings.prefs.export_dir_label", path=_esc(str(config.export_dir()))),
            _("settings.prefs.logs_dir_label", path=_esc(str(debug_log.logs_dir()))),
        ]
        console.print(Panel("\n".join(lines), title=_("settings.prefs.title"), border_style="cyan", padding=(0, 2)))

        action = questionary.select(
            _("settings.prefs.prompt"),
            choices=[
                questionary.Choice(_("settings.prefs.units_choice", units=units), value="units"),
                questionary.Choice(_("settings.prefs.checkin_choice", days=checkin_days), value="checkin"),
                questionary.Choice(
                    _("settings.prefs.autosync_choice", state=on_str if auto_sync else off_str), value="autosync"
                ),
                questionary.Choice(_("settings.prefs.stale_hours_choice", hours=stale_hours), value="stale_hours"),
                questionary.Choice(_("settings.prefs.stats_window_choice", window=default_weeks), value="stats_window"),
                questionary.Choice(_("settings.prefs.ui_language_choice", lang=ui_lang_name), value="ui_language"),
                questionary.Choice(_("settings.prefs.dirs_choice"), value="dirs"),
                questionary.Separator("  ───"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "units":
            new_units = questionary.select(
                _("settings.prefs.units_prompt"),
                choices=[
                    questionary.Choice(_("settings.prefs.units_kg"), value="kg"),
                    questionary.Choice(_("settings.prefs.units_lbs"), value="lbs"),
                ],
                default=units,
                style=STYLE,
            ).ask()
            if new_units:
                set_pref("units", new_units)
                _dlog("SETTING", "units changed", value=new_units)
                console.print(_("settings.prefs.units_saved", units=new_units))

        elif action == "checkin":
            new_days = questionary.select(
                _("settings.prefs.checkin_prompt"),
                choices=[
                    questionary.Choice(_("settings.prefs.checkin_7"), value="7"),
                    questionary.Choice(_("settings.prefs.checkin_14"), value="14"),
                    questionary.Choice(_("settings.prefs.checkin_30"), value="30"),
                ],
                default=str(checkin_days),
                style=STYLE,
            ).ask()
            if new_days:
                set_pref("goals_checkin_days", new_days)
                _dlog("SETTING", "goals_checkin_days changed", value=new_days)
                console.print(_("settings.prefs.checkin_saved", days=new_days))

        elif action == "autosync":
            new_auto = "0" if auto_sync else "1"
            set_pref("auto_sync", new_auto)
            _dlog("SETTING", "auto_sync changed", value=new_auto)
            console.print(_("settings.prefs.autosync_disabled" if auto_sync else "settings.prefs.autosync_enabled"))

        elif action == "stale_hours":
            answer = questionary.select(
                _("settings.prefs.stale_hours_prompt"),
                choices=[questionary.Choice(_("time.hours", n=h), value=str(h)) for h in (6, 12, 24, 48)],
                default=str(stale_hours) if stale_hours in (6, 12, 24, 48) else "24",
                style=STYLE,
            ).ask()
            if answer:
                set_pref("sync_stale_hours", answer)
                _dlog("SETTING", "sync_stale_hours changed", value=answer)
                console.print(_("settings.prefs.stale_hours_saved", hours=answer))

        elif action == "stats_window":
            new_window = questionary.select(
                _("settings.prefs.stats_window_prompt"),
                choices=_week_choices([4, 8, 12, 24]),
                default=default_weeks,
                style=STYLE,
            ).ask()
            if new_window:
                set_pref("default_stats_weeks", new_window)
                _dlog("SETTING", "default_stats_weeks changed", value=new_window)
                console.print(_("settings.prefs.stats_window_saved", window=new_window))

        elif action == "ui_language":
            lang_choices = [questionary.Choice(name, value=code) for code, name in _UI_LANGUAGES]
            new_code = questionary.select(
                _("settings.prefs.ui_language_prompt"),
                choices=lang_choices,
                style=STYLE,
            ).ask()
            if new_code and new_code != ui_lang_code:
                set_pref("ui_language", new_code)
                _i18n.init(new_code)
                _dlog("SETTING", "ui_language changed", value=new_code)
                new_name = dict(_UI_LANGUAGES).get(new_code, new_code)
                console.print(_("settings.prefs.ui_language_saved", lang=new_name))

        elif action == "dirs":
            _do_dirs_settings()


def _do_dirs_settings() -> None:
    """Configure the export and logs folders (global — stored in .env)."""
    import debug_log

    which = questionary.select(
        _("settings.prefs.dirs_prompt"),
        choices=[
            questionary.Choice(
                _("settings.prefs.dirs_export_choice", path=_esc(str(config.export_dir()))), value="EXPORT_DIR"
            ),
            questionary.Choice(
                _("settings.prefs.dirs_logs_choice", path=_esc(str(debug_log.logs_dir()))), value="LOGS_DIR"
            ),
            questionary.Choice(_("nav.back"), value=None),
        ],
        style=STYLE,
    ).ask()
    if not which:
        return

    current = config.EXPORT_DIR if which == "EXPORT_DIR" else config.LOGS_DIR
    raw = questionary.path(
        _("settings.prefs.dirs_path_prompt"),
        default=current,
        style=STYLE,
    ).ask()
    if raw is None:
        return

    value = raw.strip()
    if value:
        target = Path(value).expanduser()
        if not target.is_absolute():
            console.print(_("settings.prefs.dirs_not_absolute"))
            return
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            console.print(f"[red]{_esc(str(e))}[/red]")
            return
        value = str(target)

    config.set_env_values({which: value})
    config.reload_env()
    _dlog("SETTING", f"{which} changed", value=value or "(default)")
    if value:
        console.print(_("settings.prefs.dirs_saved", name=which, path=_esc(value)))
    else:
        console.print(_("settings.prefs.dirs_reset", name=which))


_EXPORT_KINDS = {
    "memories": ["chat_memories"],
    "goals": ["user_goals"],
    "measurements": ["body_measurements"],
    "full": None,  # every table in the active DB
}


def _export_data(kind: str, dest_dir: Path | None = None) -> tuple[Path, int]:
    """Dump the requested tables to a timestamped JSON file. Returns (path, total_rows)."""
    from db.goals import get_token_usage, get_token_usage_month
    from db.store import query as _query

    tables = _EXPORT_KINDS[kind]
    if tables is None:
        tables = [
            r["name"]
            for r in _query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]

    dumped: dict = {}
    total = 0
    for t in tables:
        try:
            rows = _query(f'SELECT * FROM "{t}"')
        except sqlite3.OperationalError:
            rows = []
        dumped[t] = rows
        total += len(rows)

    payload = {
        "app": "lifter",
        "kind": kind,
        "exported_at": datetime.now(UTC).isoformat(),
        "tables": dumped,
    }
    # user_preferences holds only UI settings and ai_tokens_* counters — API keys
    # live in profile.json / .env, so a full dump needs no redaction.
    if kind in ("goals", "full"):
        payload["token_usage"] = {
            "lifetime": get_token_usage(),
            "month": get_token_usage_month(),
        }

    out_dir = dest_dir or config.export_dir()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(f"Could not create the export folder at {out_dir}: {e}") from e
    path = out_dir / f"lifter-export-{kind}-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path, total


def _read_import_payload(path: Path) -> dict:
    """Load and structurally validate an export file. Raises ValueError if not importable."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(str(e)[:120]) from e
    if not isinstance(payload, dict) or payload.get("app") != "lifter":
        raise ValueError("missing app == 'lifter' marker")
    tables = payload.get("tables")
    if not isinstance(tables, dict) or not all(isinstance(v, list) for v in tables.values()):
        raise ValueError("missing/invalid 'tables' object")
    return payload


def _import_data(path: Path, payload: dict | None = None) -> dict:
    """Restore tables from an export file (replace semantics, single transaction).

    For every dumped table that exists in the current schema: delete all rows,
    then insert the dumped rows. Tables absent from the schema are skipped;
    columns unknown to the schema are dropped per row.
    """
    import db.store as store_mod  # module attrs → honors the tmp_db monkeypatch

    payload = payload or _read_import_payload(path)
    store_mod.init_db()  # the profile may have just been reset

    imported: dict = {}
    skipped_tables: list = []
    skipped_columns: dict = {}
    conn = store_mod._conn()
    try:
        with conn:
            live = {}
            for table in payload["tables"]:
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
                if not cols:
                    skipped_tables.append(table)
                    continue
                live[table] = set(cols)

            # FK checks deferred to commit — dumped tables can be inserted in
            # any order and a violation rolls the whole transaction back.
            # Must be set AFTER the table_info reads: in sqlite3's legacy
            # autocommit handling, a later PRAGMA read silently resets it.
            conn.execute("PRAGMA defer_foreign_keys = ON")

            # Wipe every target table before ANY insert — a cascade delete of an
            # old parent row must never eat freshly inserted children.
            for table in live:
                conn.execute(f'DELETE FROM "{table}"')

            for table, colset in live.items():
                inserted = 0
                for row in payload["tables"][table]:
                    keep = [c for c in row if c in colset]
                    dropped = [c for c in row if c not in colset]
                    if dropped:
                        skipped_columns[table] = sorted(set(skipped_columns.get(table, [])) | set(dropped))
                    if not keep:
                        continue
                    col_sql = ", ".join(f'"{c}"' for c in keep)
                    conn.execute(
                        f'INSERT INTO "{table}" ({col_sql}) VALUES ({", ".join("?" * len(keep))})',
                        [row[c] for c in keep],
                    )
                    inserted += 1
                imported[table] = inserted
    finally:
        conn.close()

    from render_cache import invalidate

    invalidate()
    return {
        "kind": payload.get("kind", "?"),
        "exported_at": payload.get("exported_at", "?"),
        "imported": imported,
        "total": sum(imported.values()),
        "skipped_tables": skipped_tables,
        "skipped_columns": skipped_columns,
    }


def _do_import_data() -> None:
    exports_dir = config.export_dir()
    candidates = (
        sorted(
            exports_dir.glob("lifter-export-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:15]
        if exports_dir.is_dir()
        else []
    )

    if candidates:
        choices = [questionary.Choice(f"  {p.name}  ({p.stat().st_size / 1024:,.0f} KB)", value=p) for p in candidates]
        choices += [
            questionary.Separator("  ───"),
            questionary.Choice(_("settings.dev.import.manual_choice"), value="_manual"),
            questionary.Choice(_("nav.back"), value=None),
        ]
        picked = questionary.select(_("settings.dev.import.pick_prompt"), choices=choices, style=STYLE).ask()
        if picked is None:
            return
    else:
        console.print(_("settings.dev.import.no_files", dir=_esc(str(exports_dir))))
        picked = "_manual"

    if picked == "_manual":
        raw = questionary.path(_("settings.dev.import.path_prompt"), style=STYLE).ask()
        if not raw or not raw.strip():
            return
        picked = Path(raw.strip()).expanduser()
        if not picked.is_file():
            console.print(_("settings.dev.import.not_found", path=_esc(str(picked))))
            questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()
            return

    try:
        payload = _read_import_payload(picked)
    except ValueError as e:
        console.print(_("settings.dev.import.invalid", error=_esc(str(e))))
        questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()
        return

    lines = [
        _(
            "settings.dev.import.summary_meta",
            kind=payload.get("kind", "?"),
            when=_esc(str(payload.get("exported_at", "?"))),
        ),
        "",
    ]
    for table, rows in payload["tables"].items():
        try:
            existing = query(f'SELECT COUNT(*) AS n FROM "{table}"')[0]["n"]
            lines.append(_("settings.dev.import.summary_row", table=table, count=len(rows), existing=existing))
        except sqlite3.OperationalError:
            lines.append(_("settings.dev.import.summary_unknown", table=table))
    lines += ["", _("settings.dev.import.warning")]
    console.print(
        Panel("\n".join(lines), title=_("settings.dev.import.summary_title"), border_style="red", padding=(0, 2))
    )

    if not questionary.confirm(_("settings.dev.import.confirm1"), default=False, style=STYLE).ask():
        return
    if (
        payload.get("kind") == "full"
        and not questionary.confirm(_("settings.dev.import.confirm2"), default=False, style=STYLE).ask()
    ):
        return

    try:
        summary = _import_data(picked, payload)
    except Exception as e:
        console.print(_("settings.dev.import.failed", error=_esc(str(e))))
        questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()
        return

    _dlog("IMPORT", "data imported", kind=summary["kind"], rows=summary["total"], path=str(picked))
    console.print(_("settings.dev.import.done", rows=summary["total"], path=_esc(str(picked))))
    if summary["skipped_columns"]:
        n_cols = sum(len(v) for v in summary["skipped_columns"].values())
        console.print(_("settings.dev.import.skipped_cols_note", count=n_cols))

    if summary["kind"] == "full":
        # restored prefs carry process-level state — re-apply them
        import debug_log
        import i18n as _i18n

        _i18n.init(get_pref("ui_language") or config.DEFAULT_LANGUAGE)
        debug_log.enable(get_pref("debug_logging") == "1")
        config.apply_ai_overrides()
    questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()


def _do_export_data() -> None:
    while True:
        console.clear()
        action = questionary.select(
            _("settings.dev.export.prompt"),
            choices=[
                questionary.Choice(_("settings.dev.export.memories_choice"), value="memories"),
                questionary.Choice(_("settings.dev.export.goals_choice"), value="goals"),
                questionary.Choice(_("settings.dev.export.measurements_choice"), value="measurements"),
                questionary.Choice(_("settings.dev.export.full_choice"), value="full"),
                questionary.Separator("  ───"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        try:
            path, rows = _export_data(action)
        except Exception as e:
            console.print(_("settings.dev.export.failed", error=_esc(str(e))))
            questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()
            continue
        _dlog("EXPORT", "data exported", kind=action, rows=rows)
        console.print(_("settings.dev.export.done", rows=rows, path=_esc(str(path))))
        if rows == 0:
            console.print(_("settings.dev.export.empty_note"))
        questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()


def _do_developer_settings() -> None:
    import debug_log

    while True:
        console.clear()
        debug_on = get_pref("debug_logging") == "1"
        on_str = _("settings.on")
        off_str = _("settings.off")
        try:
            db_desc = f"{config.DB_PATH} ({config.DB_PATH.stat().st_size / 1024:,.0f} KB)"
        except OSError:
            db_desc = str(config.DB_PATH)

        lines = [
            _("settings.dev.version_label", version=_app_version()),
            _("settings.dev.debug_label", state=on_str if debug_on else off_str),
            _("settings.dev.logs_dir_label", path=_esc(str(debug_log.logs_dir()))),
            _("settings.dev.db_label", path=_esc(db_desc)),
            _("settings.dev.hevy_sync_label", status=_sync_status_str("last_sync_result")),
            _("settings.dev.fit_sync_label", status=_sync_status_str("fit_last_sync_result")),
        ]
        console.print(Panel("\n".join(lines), title=_("settings.dev.title"), border_style="cyan", padding=(0, 2)))

        action = questionary.select(
            _("settings.dev.prompt"),
            choices=[
                questionary.Choice(_("settings.dev.export_choice"), value="export"),
                questionary.Choice(_("settings.dev.import_choice"), value="import"),
                questionary.Choice(_("settings.dev.ai_context_choice"), value="ai_context"),
                questionary.Choice(_("settings.dev.db_info_choice"), value="db_info"),
                questionary.Choice(
                    _("settings.dev.debug_choice", state=on_str if debug_on else off_str), value="debug"
                ),
                questionary.Choice(_("settings.dev.clear_logs_choice"), value="clear_logs"),
                questionary.Separator("  ───"),
                questionary.Choice(_("settings.dev.reset_choice"), value="reset"),
                questionary.Separator("  ───"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "export":
            _do_export_data()

        elif action == "import":
            _do_import_data()

        elif action == "ai_context":
            # Builds the exact <training_data> block the chat sends to the AI
            # provider — entirely local, no API call.
            from ai.coach import _build_context

            slim = get_pref("ai_chat_slim") != "0"
            include_routines = get_pref("ai_include_routines") != "0"
            ctx = _build_context(8, slim=slim, include_routine=include_routines)
            out_dir = config.export_dir()
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"lifter-ai-context-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.md"
            path.write_text(ctx, encoding="utf-8")
            _dlog("EXPORT", "AI context preview written", chars=len(ctx))
            console.print(
                _(
                    "settings.dev.ai_context_done",
                    path=_esc(str(path)),
                    chars=f"{len(ctx):,}",
                    tokens=f"{len(ctx) // 4:,}",
                )
            )
            questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()

        elif action == "db_info":
            table_names = [
                r["name"]
                for r in query(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            info_lines = [
                _("settings.dev.db_info_row", table=t, count=query(f'SELECT COUNT(*) AS n FROM "{t}"')[0]["n"])
                for t in table_names
            ]
            console.print(
                Panel("\n".join(info_lines), title=_("settings.dev.db_info_title"), border_style="cyan", padding=(0, 2))
            )
            questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()

        elif action == "debug":
            new_val = not debug_on
            set_pref("debug_logging", "1" if new_val else "0")
            debug_log.enable(new_val)
            _dlog("SETTING", "debug_logging changed", value="on" if new_val else "off")
            if new_val:
                console.print(_("settings.dev.debug_enabled", logs_dir=debug_log.logs_dir()))
            else:
                console.print(_("settings.dev.debug_disabled"))
            questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()

        elif action == "clear_logs":
            log_files = sorted(debug_log.logs_dir().glob("debug-*.log"))
            if confirm_destructive(_("settings.dev.clear_logs_confirm", count=len(log_files))):
                for f in log_files:
                    f.unlink(missing_ok=True)
                _dlog("SETTING", "debug logs cleared", count=len(log_files))
                console.print(_("settings.dev.clear_logs_done", count=len(log_files)))
                questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()

        elif action == "reset":
            _do_data_reset()


def _do_about() -> None:
    console.clear()
    console.print(
        Panel(
            _("about.body", version=_app_version()),
            title=_("about.title"),
            border_style="cyan",
            padding=(1, 3),
        )
    )
    questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()


def _do_settings() -> None:
    while True:
        console.clear()
        action = questionary.select(
            _("settings.menu_prompt"),
            choices=[
                questionary.Choice(_("settings.profiles_choice"), value="profiles"),
                questionary.Choice(_("settings.profile_choice"), value="profile"),
                questionary.Choice(_("settings.prefs_choice"), value="prefs"),
                questionary.Choice(_("settings.ai_choice"), value="ai"),
                questionary.Choice(_("settings.dev_choice"), value="dev"),
                questionary.Separator("  ───────────────────────────────────────"),
                questionary.Choice(_("settings.about_choice"), value="about"),
                questionary.Separator("  ───────────────────────────────────────"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return
        if action == "profiles":
            _do_profiles_menu()
        elif action == "profile":
            _do_profile_settings()
        elif action == "prefs":
            _do_preferences_settings()
        elif action == "ai":
            _do_ai_settings()
        elif action == "dev":
            _do_developer_settings()
        elif action == "about":
            _do_about()


# ── google fit ────────────────────────────────────────────────────────────────


def _render_recovery_panel() -> None:
    """Show a compact recovery panel if Fit data exists."""
    try:
        from fit.analytics import activity_summary, recovery_score, sleep_summary
        from fit.auth import is_connected

        if not is_connected():
            return
        rec = recovery_score(3)
        sleep = sleep_summary(7)
        activity = activity_summary(7)
        if not rec and not sleep:
            return
        parts = []
        if rec:
            parts.append(_("fit.metric_recovery", color=rec["color"], score=rec["score"], label=rec["label"]))
        if sleep.get("avg_hours"):
            parts.append(_("fit.metric_sleep", hours=sleep["avg_hours"]))
        if activity.get("avg_steps"):
            parts.append(_("fit.metric_steps", steps=f"{int(activity['avg_steps']):,}"))
        if activity.get("resting_hr"):
            parts.append(_("fit.metric_rhr", rhr=activity["resting_hr"]))
        if parts:
            console.print(
                Panel("  ·  ".join(parts), title=_("fit.recovery_panel_title"), border_style="green", padding=(0, 2))
            )
            console.print()
    except Exception:
        pass


def _do_fit():
    from fit.auth import disconnect, is_connected

    action = questionary.select(
        _("fit.menu_prompt"),
        choices=[
            questionary.Choice(_("fit.sync_choice"), value="sync"),
            questionary.Choice(_("fit.connect_choice"), value="connect"),
            questionary.Choice(_("fit.view_choice"), value="view"),
            questionary.Choice(_("fit.disconnect_choice"), value="disconnect"),
        ],
        style=STYLE,
    ).ask()
    if not action:
        return

    if action == "connect":
        _fit_setup()

    elif action == "sync":
        if not is_connected():
            console.print(_("fit.not_connected_warning"))
            return
        days_str = questionary.select(
            _("fit.days_prompt"),
            choices=_day_choices([7, 14, 30, 90]),
            default="30 days",
            style=STYLE,
        ).ask()
        if not days_str:
            return
        days = int(days_str.split()[0])
        try:
            from fit.sync import sync_fit

            with console.status(_("fit.syncing_n_days", days=days), spinner="dots"):
                counts = sync_fit(days=days)
            console.print(
                Panel(
                    _("fit.sync_counts", daily=counts["daily_days"], sleep=counts["sleep_sessions"]),
                    title=_("fit.sync_complete_title"),
                    border_style="green",
                )
            )
            _render_recovery_panel()
        except Exception as e:
            _dlog("ERROR", f"Google Fit sync failed: {type(e).__name__}", error=str(e)[:200])
            console.print(_("error.fit_sync_failed", error=e))

    elif action == "view":
        if not is_connected():
            console.print(_("fit.not_connected_short"))
            return
        _render_fit_dashboard()

    elif action == "disconnect" and confirm_destructive(_("fit.disconnect_confirm")):
        disconnect()
        _dlog("SETTING", "Google Fit disconnected")
        console.print(_("fit.disconnected"))


def _fit_setup() -> None:
    from fit.auth import credentials_file

    console.rule(_("fit.connect_rule"))
    console.print(_("fit.setup_instructions"))

    if not credentials_file().exists():
        raw = questionary.path(_("fit.credentials_path_prompt"), style=STYLE).ask()
        if not raw or not raw.strip():
            return
        source = Path(raw.strip()).expanduser()
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
            assert isinstance(payload, dict) and ("installed" in payload or "web" in payload)
        except Exception:
            console.print(_("fit.credentials_invalid"))
            return
        import shutil as _shutil

        import paths as _paths

        _paths.ensure_dirs()
        _shutil.copy2(source, _paths.FIT_CREDENTIALS_FILE)
        _paths.FIT_CREDENTIALS_FILE.chmod(0o600)
        console.print(_("fit.credentials_saved", path=_esc(str(_paths.FIT_CREDENTIALS_FILE))))

    if not questionary.confirm(_("fit.ready_to_auth"), default=True, style=STYLE).ask():
        return

    try:
        from fit.auth import get_credentials

        get_credentials()
        _dlog("SETTING", "Google Fit connected")
        console.print(_("fit.connected_ok"))
        console.print(_("fit.connected_hint"))
    except FileNotFoundError as e:
        _dlog("ERROR", "Google Fit connect failed: credentials file not found")
        console.print(f"\n[red]{e}[/red]")  # safe: our own message, no secrets
    except Exception as e:
        _dlog("ERROR", f"Google Fit connect failed: {type(e).__name__}", error=str(e)[:200])
        console.print(_("error.fit_auth_failed"))
        console.print(f"[dim]{type(e).__name__}[/dim]")


def _render_fit_dashboard() -> None:
    from fit.analytics import activity_summary, recovery_score, sleep_summary

    console.rule(_("fit.dashboard_rule"))

    rec = recovery_score(3)
    if rec:
        score = rec["score"]
        bar_w = int(score / 100 * 30)
        bar = f"[{rec['color']}]{'█' * bar_w}[/{rec['color']}][dim]{'░' * (30 - bar_w)}[/dim]"
        console.print(
            f"\n  {_('fit.recovery_score_label')}  {bar}  [{rec['color']}]{score}/100  {rec['label']}[/{rec['color']}]\n"
        )

    for days in (7, 14):
        sleep = sleep_summary(days)
        activity = activity_summary(days)
        if not sleep and not activity:
            continue
        console.rule(f"[dim]{_('fit.last_n_days', n=days)}[/dim]")
        t = Table(box=box.SIMPLE)
        t.add_column(_("fit.col_metric"), style="bold")
        t.add_column(_("fit.col_value"), justify="right")
        if sleep.get("avg_hours"):
            t.add_row(_("fit.avg_sleep"), f"{sleep['avg_hours']}h/night")
            t.add_row(_("fit.last_night"), f"{sleep.get('last_night_hours')}h")
            t.add_row(_("fit.nights_7plus"), f"{sleep['nights_7plus_hours']}/{sleep['nights_tracked']}")
        if activity.get("avg_steps"):
            t.add_row(_("fit.avg_steps"), f"{int(activity['avg_steps']):,}")
        if activity.get("avg_calories"):
            t.add_row(_("fit.avg_calories"), f"{int(activity['avg_calories']):,} kcal")
        if activity.get("resting_hr"):
            t.add_row(_("fit.resting_hr"), f"{activity['resting_hr']} bpm")
        if activity.get("avg_active_minutes"):
            t.add_row(_("fit.avg_active_minutes"), str(int(activity["avg_active_minutes"])))
        console.print(t)


# ── first-run & weekly check-in ───────────────────────────────────────────────


def _check_stale_sync() -> None:
    """Auto-sync or prompt if Hevy/Google Fit data is older than 24 hours."""
    from db.store import get_sync_state

    auto_sync = get_pref("auto_sync") == "1"

    def _is_stale(key: str) -> bool:
        val = get_sync_state(key)
        if not val:
            return True  # never synced — the data is as stale as it gets
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return (datetime.now(UTC) - dt).total_seconds() > _stale_seconds()
        except Exception:
            return False

    # Only consider Hevy when a key is configured — otherwise a fresh profile
    # would be nagged to sync before it can possibly succeed.
    stale_hevy = bool(config.HEVY_API_KEY) and _is_stale("last_sync")

    from fit.auth import is_connected as _fit_connected

    fit_ok = _fit_connected()
    stale_fit = fit_ok and _is_stale("fit_last_sync")

    if not stale_hevy and not stale_fit:
        _dlog("SYNC", "Data is fresh, no sync needed")
        return

    console.print()

    if stale_hevy:
        if auto_sync:
            try:
                _dlog("SYNC", "Hevy auto-sync triggered (data stale >24h)")
                console.print(_("sync.auto_syncing_hevy"))
                client = _require_hevy()
                if client:
                    counts = incremental_sync(client)
                    console.print(_("sync.auto_synced_hevy", updated=counts["updated"], deleted=counts["deleted"]))
            except Exception as e:
                _dlog("SYNC", "Hevy auto-sync error", error=str(e)[:200])
                console.print(_("sync.auto_sync_hevy_failed", error=e))
        elif questionary.confirm(_("sync.stale_hevy_prompt"), default=True, style=STYLE).ask():
            _dlog("SYNC", "User accepted Hevy sync prompt")
            client = _require_hevy()
            if client:
                try:
                    counts = incremental_sync(client)
                    console.print(_("sync.hevy_done", updated=counts["updated"], deleted=counts["deleted"]))
                except Exception as e:
                    import debug_log

                    debug_log.error("SYNC", "Hevy sync (stale prompt) failed", exc=e)
                    console.print(_("sync.auto_sync_hevy_failed", error=e))
        else:
            _dlog("SYNC", "User declined Hevy sync prompt")

    if stale_fit:
        if auto_sync:
            try:
                _dlog("SYNC", "Google Fit auto-sync triggered (data stale >24h)")
                from fit.sync import sync_fit

                with console.status(_("sync.auto_syncing_fit"), spinner="dots"):
                    counts = sync_fit(days=30)
                console.print(
                    _("sync.auto_synced_fit", daily_days=counts["daily_days"], sleep_sessions=counts["sleep_sessions"])
                )
            except Exception as e:
                _dlog("SYNC", "Google Fit auto-sync error", error=str(e)[:200])
                console.print(_("sync.auto_sync_fit_failed", error=e))
        elif questionary.confirm(_("sync.stale_fit_prompt"), default=True, style=STYLE).ask():
            _dlog("SYNC", "User accepted Google Fit sync prompt")
            try:
                from fit.sync import sync_fit

                with console.status(_("sync.fit_syncing"), spinner="dots"):
                    counts = sync_fit(days=90)
                console.print(
                    _("sync.fit_done", daily_days=counts["daily_days"], sleep_sessions=counts["sleep_sessions"])
                )
            except Exception as e:
                console.print(_("error.fit_sync_failed", error=e))
        else:
            _dlog("SYNC", "User declined Google Fit sync prompt")


def _check_goals_and_checkin() -> None:
    if should_ask_goals():
        goals = get_goals()
        if not goals:
            # First time ever
            if questionary.confirm(_("goals.set_now_first_run"), default=True, style=STYLE).ask():
                _dlog("GOAL", "First-run goals wizard started")
                run_goals_wizard()
            else:
                _dlog("GOAL", "First-run goals wizard declined")
        else:
            # Weekly check-in
            _dlog("GOAL", "Weekly check-in triggered")
            _weekly_checkin()


def _check_goal_celebrations() -> None:
    """One-time festive panel for goals achieved since the last celebration."""
    from db.goals import get_uncelebrated_achievements, mark_achievements_celebrated

    compute_goal_progress()  # freshly-synced data may mark newly-achieved goals
    achieved = get_uncelebrated_achievements()
    if not achieved:
        return
    lines = [_("goals.celebrate.intro"), ""]
    for goal in achieved:
        lines.append(_("goals.celebrate.item", description=_esc(goal["description"])))
    lines += ["", _("goals.celebrate.outro")]
    console.print()
    console.print(Panel("\n".join(lines), title=_("goals.celebrate.panel_title"), border_style="green", padding=(1, 2)))
    mark_achievements_celebrated()
    _dlog("GOAL", "Goal celebration shown", count=len(achieved))
    _pause()


def _check_auto_report() -> None:
    """Generate a coaching report automatically once every 7 days at startup."""
    if not should_auto_report():
        return
    # Silent AI check — never nag at startup when AI isn't configured.
    if not _ai_configured():
        return
    # Nothing to report on yet — skip until there's training data.
    from db.store import query

    if not query("SELECT 1 FROM workouts LIMIT 1"):
        return

    console.print()
    console.print(_("coach.auto_report_intro"))
    _dlog("AI", "Auto coaching report triggered (7-day)")
    # Analysis only — creating a routine stays an explicit user action.
    if _run_report(weeks=_report_weeks(), generate_routine=False):
        _pause()


# ── main loop ─────────────────────────────────────────────────────────────────

ACTIONS = {
    "sync": _do_sync,
    "stats": _do_stats,
    "progress": _do_progress,
    "records": _do_records,
    "goals": _do_goals,
    "body": _do_body_entry,
    "fit": _do_fit,
    "coach": _do_coach,
    "chat": _do_chat,
    "settings": _do_settings,
}


def _run_action(choice: str, action) -> object:
    """Run a menu action behind the app-wide safety net.

    Error-handling convention: RuntimeError carries a user-ready message
    (raised by the Hevy/Fit/AI layers); anything else is a bug — shown as a
    generic panel and recorded with a full traceback via debug_log.error().
    Returns the action's result (NO_PAUSE or None).
    """
    import debug_log

    try:
        return action()
    except (KeyboardInterrupt, EOFError):
        return None
    except RuntimeError as e:
        debug_log.error("APP", f"Action '{choice}' failed", exc=e)
        console.print(f"[red]{_esc(str(e))}[/red]")
        return None
    except Exception as e:
        debug_log.error("APP", f"Unhandled error in '{choice}'", exc=e)
        hint = ""
        if isinstance(e, sqlite3.OperationalError) and any(word in str(e).lower() for word in ("locked", "malformed")):
            hint = "\n" + _("error.db_hint")
        console.print(
            Panel(
                _("error.unexpected", exc_type=type(e).__name__, log_dir=_esc(str(debug_log.logs_dir()))) + hint,
                border_style="red",
                padding=(0, 2),
            )
        )
        return None


def _build_menu() -> tuple:
    try:
        from fit.auth import is_connected as _fit_connected

        fit_label = _("menu.fit_connected") if _fit_connected() else _("menu.fit_disconnected")
    except Exception:
        fit_label = _("menu.fit_connected")

    last_action = get_pref("last_menu_action")
    items = [
        questionary.Choice(_("menu.sync"), value="sync"),
        questionary.Choice(_("menu.chat"), value="chat"),
        questionary.Separator("  ──────────────────────────────────"),
        questionary.Choice(_("menu.goals"), value="goals"),
        questionary.Choice(_("menu.body"), value="body"),
        questionary.Choice(_("menu.stats"), value="stats"),
        questionary.Choice(_("menu.progress"), value="progress"),
        questionary.Choice(_("menu.records"), value="records"),
        questionary.Separator("  ──────────────────────────────────"),
        questionary.Choice(_("menu.coach"), value="coach"),
        questionary.Choice(fit_label, value="fit"),
        questionary.Separator("  ──────────────────────────────────"),
        questionary.Choice(_("menu.settings"), value="settings"),
        questionary.Choice(_("menu.exit"), value="exit"),
    ]
    default = next((c for c in items if isinstance(c, questionary.Choice) and c.value == last_action), None)
    return items, default


def _bootstrap_profiles() -> None:
    """Select or create a profile before any DB operations."""
    import shutil as _shutil
    from pathlib import Path as _Path

    from profile_mgr import (
        PROFILES_DIR,
        PROFILES_FILE,
        activate_profile,
        create_profile,
        get_active_slug,
        list_profiles,
        set_active_slug,
    )

    project_dir = _Path(__file__).resolve().parent
    old_db = project_dir / "hevy.db"
    old_token = project_dir / "fit_token.json"

    # Migration: existing single-user hevy.db with no profiles.json yet
    if not PROFILES_FILE.exists() and old_db.exists():
        console.print()
        console.print(
            Panel(
                _("migration.panel_body"),
                title=_("migration.panel_title"),
                border_style="cyan",
                padding=(0, 2),
            )
        )
        name = (
            questionary.text(
                _("migration.name_prompt"),
                default="Default",
                style=STYLE,
            ).ask()
            or "Default"
        ).strip()

        profile = create_profile(name, hevy_api_key=config.HEVY_API_KEY)
        slug = profile["slug"]
        profile_dir = PROFILES_DIR / slug
        _shutil.move(str(old_db), profile_dir / "hevy.db")
        if old_token.exists():
            _shutil.move(str(old_token), profile_dir / "fit_token.json")
        set_active_slug(slug)
        activate_profile(slug)
        _dlog("PROFILE", "Single-user data migrated to profile", slug=slug)
        console.print(_("migration.done", name=_esc(name)))
        console.print()
        return

    profiles = list_profiles()

    if not profiles:
        # First run — prompt for name, Hevy API key, and language
        console.print()
        console.rule(_("welcome.rule"))
        name = (
            questionary.text(
                _("welcome.name_prompt"),
                default="Athlete",
                style=STYLE,
            ).ask()
            or "Athlete"
        ).strip()
        api_key = (
            questionary.text(
                _("welcome.api_key_prompt"),
                style=STYLE,
            ).ask()
            or ""
        ).strip()
        lang_choices = [questionary.Choice(lname, value=code) for code, lname in _UI_LANGUAGES]
        lang_code = (
            questionary.select(
                _("welcome.language_prompt"),
                choices=lang_choices,
                style=STYLE,
            ).ask()
            or "en"
        )
        profile = create_profile(name, hevy_api_key=api_key)
        activate_profile(profile["slug"])
        init_db()
        set_pref("ui_language", lang_code)
        import i18n as _i18n

        _i18n.init(lang_code)
        _dlog("PROFILE", "First run: profile created", slug=profile["slug"])
        # Baseline body metrics — the user may not have Google Fit connected yet.
        _onboard_body_metrics()
        console.print()
        return

    if len(profiles) == 1:
        activate_profile(profiles[0]["slug"])
        return

    # Multiple profiles — show selector
    last = get_active_slug()
    choices = []
    for p in profiles:
        suffix = _("profiles.last_used_suffix") if p["slug"] == last else ""
        choices.append(questionary.Choice(f"  {p['name']}{suffix}", value=p["slug"]))
    choices.append(questionary.Separator("  ──────────────────────────────────"))
    choices.append(questionary.Choice(_("profiles.new_profile_choice"), value="_new"))

    console.clear()
    slug = questionary.select(_("profiles.select_prompt"), choices=choices, style=STYLE).ask()

    if not slug:
        slug = last or profiles[0]["slug"]
    elif slug == "_new":
        slug = _do_create_profile_flow()

    _dlog("PROFILE", "Profile selected at startup", slug=slug, total_profiles=len(profiles))
    set_active_slug(slug)
    activate_profile(slug)


def main():
    import sys

    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        print(f"lifter {_app_version()}")
        return

    import paths

    paths.ensure_dirs()
    moved = paths.migrate_legacy_layout()
    if moved:
        config.reload_env()  # the .env file may have just moved

    import i18n as _i18n

    _i18n.init(config.DEFAULT_LANGUAGE)  # Phase 1: before profile selector

    if moved:
        console.print(
            Panel(
                "\n".join(_esc(m) for m in moved),
                title=_("migration.xdg_title"),
                border_style="cyan",
                padding=(0, 2),
            )
        )
        console.print(
            _("migration.xdg_done", config_dir=_esc(str(paths.CONFIG_DIR)), data_dir=_esc(str(paths.DATA_DIR)))
        )
    _bootstrap_profiles()
    init_db()
    config.apply_ai_overrides()  # per-profile provider/model prefs
    ui_lang = get_pref("ui_language") or config.DEFAULT_LANGUAGE
    _i18n.init(ui_lang)  # Phase 2: after profile DB is open
    import debug_log

    debug_log.init()
    import config as _cfg
    from profile_mgr import get_active_slug

    _dlog("APP", "Lifter started", profile=get_active_slug() or "none", provider=_cfg.AI_PROVIDER, model=_cfg.AI_MODEL)
    from db.goals import maybe_rollover_tokens

    maybe_rollover_tokens()  # reset monthly token counter if the period rolled over
    try:
        _check_goals_and_checkin()
        _check_body_checkin()
        _check_stale_sync()
        _check_goal_celebrations()
        _check_auto_report()
    except (KeyboardInterrupt, EOFError):
        pass
    except Exception as e:
        # A broken startup check must never keep the user from the menu.
        debug_log.error("APP", "Startup check failed", exc=e)
        console.print(_("error.startup_check_failed", exc_type=type(e).__name__))

    while True:
        console.clear()
        _show_header()
        _render_snapshot_panel()
        menu_items, menu_default = _build_menu()

        choice = questionary.select(
            _("menu.prompt"),
            choices=menu_items,
            default=menu_default,
            style=STYLE,
        ).ask()

        if choice is None or choice == "exit":
            console.print(_("menu.goodbye"))
            _dlog("APP", "Lifter exited")
            break

        _dlog("MENU", f"Selected: {choice}")
        set_pref("last_menu_action", choice)
        console.clear()
        action = ACTIONS.get(choice)
        result = _run_action(choice, action) if action else None

        if result is not NO_PAUSE:
            _pause()


if __name__ == "__main__":
    main()
