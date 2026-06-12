"""hevy — interactive personal Hevy workout client."""
import json
from datetime import datetime, timezone
from typing import Optional

import questionary
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich import box

from rich.markup import escape as _esc
import config
from i18n import _
from config import AI_PROVIDER, get_provider_api_key
from db.store import init_db, query
from db.goals import (
    get_pref, set_pref, get_goals, clear_goals, save_goal,
    should_ask_goals, mark_goals_asked, compute_goal_progress,
)
from hevy.client import HevyClient
from hevy.sync import full_sync, incremental_sync
from analytics.volume import muscle_group_summary, sets_per_muscle_per_week, weekly_volume
from analytics.progression import detect_plateaus, top_progressions, exercise_progression
from analytics.frequency import workout_frequency, muscle_group_frequency
from analytics.records import all_time_records, recent_prs, body_measurement_trend

console = Console()

STYLE = questionary.Style([
    ("qmark",       "fg:#00d7ff bold"),
    ("question",    "bold"),
    ("answer",      "fg:#00d7ff bold"),
    ("pointer",     "fg:#00d7ff bold"),
    ("highlighted", "fg:#00d7ff bold"),
    ("selected",    "fg:#00d7ff"),
    ("separator",   "fg:#555555"),
    ("instruction", "fg:#555555 italic"),
    ("checkbox",    "fg:#00d7ff"),
])

# ── helpers ───────────────────────────────────────────────────────────────────

def _dlog(category: str, msg: str, **kv) -> None:
    """Forward to debug_log.log without ever raising."""
    try:
        import debug_log
        debug_log.log(category, msg, **kv)
    except Exception:
        pass


def _time_ago(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        secs = int((datetime.now(timezone.utc) - dt).total_seconds())
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return "unknown"


def _fmt_duration(start_iso: str, end_iso: str) -> str:
    try:
        s = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        return f"{int((e - s).total_seconds() / 60)} min"
    except Exception:
        return ""


def _require_hevy() -> Optional[HevyClient]:
    if not config.HEVY_API_KEY:
        console.print(_("error.hevy_api_key_not_set"))
        return None
    return HevyClient()


def _require_ai() -> bool:
    if AI_PROVIDER == "bedrock":
        return True  # uses boto3 env credentials — no API key to check here
    key = get_provider_api_key()
    if not key:
        key_names = {
            "gemini":     "GEMINI_API_KEY",
            "claude":     "ANTHROPIC_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "groq":       "GROQ_API_KEY",
            "github":     "GITHUB_TOKEN",
        }
        var = key_names.get(AI_PROVIDER, "the relevant API key")
        console.print(_("error.ai_key_not_set", var=var, provider=AI_PROVIDER))
        return False
    return True


def _pause():
    console.print()
    questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()


# ── unit helpers ──────────────────────────────────────────────────────────────

def _get_units() -> str:
    return get_pref("units") or "kg"


def _kg_to_lbs(kg: float) -> float:
    return round(float(kg) * 2.20462, 1)


def _fmt_weight(kg_val) -> str:
    if kg_val is None:
        return "—"
    val = float(kg_val)
    if _get_units() == "lbs":
        lbs = _kg_to_lbs(val)
        return f"{int(lbs) if lbs == int(lbs) else lbs} lbs"
    return f"{int(val) if val == int(val) else val} kg"


# ── score & muscle-distribution helpers ──────────────────────────────────────

_MUSCLE_GROUPS: dict = {
    "Chest":     ["chest", "pectorals"],
    "Back":      ["lats", "upper_back", "lower_back", "trapezius"],
    "Legs":      ["quadriceps", "hamstrings", "glutes", "calves", "hip_flexors"],
    "Shoulders": ["shoulders", "deltoids"],
    "Arms":      ["biceps", "triceps", "forearms"],
    "Core":      ["abdominals", "core", "obliques"],
    "Cardio":    ["cardio", "full_body"],
}


def _sets_by_group(weeks: int = 4) -> dict:
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


def _score_color(score: int) -> str:
    if score >= 80:
        return "green"
    if score >= 60:
        return "cyan"
    if score >= 40:
        return "yellow"
    return "red"


def _fmt_score_bar(label: str, score: int, bar_width: int = 12) -> str:
    color = _score_color(score)
    filled = max(1, int(score / 100 * bar_width))
    bar = f"[{color}]{'█' * filled}[/{color}][dim]{'░' * (bar_width - filled)}[/dim]"
    return f"[bold]{label:<10}[/bold] {bar}  [{color}][bold]{score:>3}[/bold][/{color}]"


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
            score_lines.append(_fmt_score_bar("Training", int(ws_raw)))
        if hs_raw:
            score_lines.append(_fmt_score_bar("Health", int(hs_raw)))
        if cs_raw:
            score_lines.append(_fmt_score_bar("Overall", int(cs_raw)))
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

    # Compact goal progress
    from db.goals import compute_goal_progress
    progress = compute_goal_progress()
    numeric = [g for g in progress if g.get("pct") is not None and not g["achieved"]]
    achieved = [g for g in progress if g["achieved"]]
    if numeric or achieved:
        lines.append(_("snapshot.goals_title"))
        for g in numeric[:4]:
            pct = float(g["pct"])
            color = _score_color(int(pct))
            bw = max(1, int(pct / 100 * 8))
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

    console.print(Panel(
        "\n".join(lines).strip(),
        title=_("snapshot.panel_title"),
        border_style="dim",
        padding=(0, 2),
    ))
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
            """SELECT MAX(ws.weight_kg * (1 + ws.reps / 30.0)) as e1rm
               FROM workout_sets ws WHERE ws.exercise_template_id = ?
               AND ws.type='normal' AND ws.weight_kg IS NOT NULL""",
            (template_id,),
        )
        current_e1rm_kg = round(rows[0]["e1rm"], 1) if rows and rows[0]["e1rm"] else 0
        current_display = _fmt_weight(current_e1rm_kg)

        target_str = questionary.text(
            _("wizard.lift_target_prompt", units=units, current=current_display),
            style=STYLE,
            validate=lambda v: (v == "" or v.replace(".", "").isdigit()) or _("validate.enter_number"),
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
        validate=lambda v: v.replace(".", "").isdigit() or _("validate.enter_number"),
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
        validate=lambda v: v.replace(".", "").isdigit() or _("validate.enter_number"),
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
        "chest", "lats", "upper_back", "shoulders", "biceps", "triceps",
        "quadriceps", "hamstrings", "glutes", "calves", "abdominals",
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
        name = questionary.text(_("wizard.name_prompt"), style=STYLE).ask()
        if name:
            set_pref("display_name", name.strip())

    greet = _("wizard.greeting_update", name=name) if is_update else _("wizard.greeting_new", name=name)
    console.print(f"\n  [bold cyan]{greet}[/bold cyan]\n")

    selected = questionary.checkbox(
        _("wizard.select_goals"),
        choices=[
            questionary.Choice(_("wizard.goal_lift"),        value="lift_pr"),
            questionary.Choice(_("wizard.goal_frequency"),   value="frequency"),
            questionary.Choice(_("wizard.goal_weight_loss"), value="weight_loss"),
            questionary.Choice(_("wizard.goal_weight_gain"), value="weight_gain"),
            questionary.Choice(_("wizard.goal_body_fat"),    value="body_fat"),
            questionary.Choice(_("wizard.goal_volume"),      value="volume"),
            questionary.Choice(_("wizard.goal_custom"),      value="custom"),
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
            questionary.Choice(_("weekly.keep"),   value="keep"),
            questionary.Choice(_("weekly.update"), value="update"),
            questionary.Choice(_("weekly.skip"),   value="skip"),
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
        filled = max(1, int(pct / 100 * 22))
        bar_color = "green" if pct >= 80 else "yellow" if pct >= 50 else "red"
        bar = f"[{bar_color}]{'█' * filled}[/{bar_color}][dim]{'░' * (22 - filled)}[/dim]"

        current = g.get("current")
        target = g.get("target")
        unit = g.get("unit", "")

        if current is not None and target is not None:
            if unit == "kg":
                detail = f"  {_fmt_weight(current)} → {_fmt_weight(target)}  ({pct:.0f}%)"
            else:
                detail = f"  {current} {unit} → {target} {unit}  ({pct:.0f}%)"
        else:
            detail = f"  {pct:.0f}%"

        lines.append(f"  [bold]{g['description']}[/bold]")
        lines.append(f"  {bar}{detail}")
        lines.append("")

    if lines:
        console.print(Panel(
            "\n".join(lines).rstrip(),
            title=_("goals.progress_title"),
            border_style="yellow",
            padding=(0, 1),
        ))


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
            """SELECT we.title, we.exercise_template_id,
                      ws.weight_kg, ws.reps,
                      ws.weight_kg * (1 + ws.reps / 30.0) as e1rm
               FROM workout_exercises we
               JOIN workout_sets ws ON ws.workout_exercise_id = we.id
               WHERE we.workout_id = ? AND ws.type = 'normal'
                 AND ws.weight_kg IS NOT NULL AND ws.reps IS NOT NULL AND ws.reps > 0
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
                """SELECT MAX(ws.weight_kg * (1 + ws.reps / 30.0)) as top
                   FROM workout_sets ws WHERE ws.exercise_template_id = ?
                   AND ws.type='normal' AND ws.weight_kg IS NOT NULL AND ws.workout_id != ?""",
                (ex["exercise_template_id"], wid),
            )
            is_pr = ex["e1rm"] > (prev[0]["top"] or 0) if prev else False
            pr_badge = "  [bold yellow]★ PR[/bold yellow]" if is_pr else ""
            lines.append(f"  [bold]{name}[/bold]  {_fmt_weight(ex['weight_kg'])} × {ex['reps']} reps{pr_badge}")

        if not lines:
            bw = query("SELECT DISTINCT we.title FROM workout_exercises we WHERE we.workout_id = ?", (wid,))
            lines = [f"  {e['title']}" for e in bw]

        console.print(Panel(
            "\n".join(lines) if lines else "  (no sets logged)",
            title=f"[bold cyan]{w['title']}[/bold cyan]  [dim]{date_str} · {duration}[/dim]",
            border_style="cyan",
            padding=(0, 1),
        ))


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
            delta = f" [{color}]{'+'if pct>=0 else ''}{pct:.0f}%[/{color}]"
        else:
            delta = " [dim]new[/dim]"
        console.print(f"    {muscle:<14} [cyan]{bar}[/cyan] {_fmt_weight(curr):>12}{delta}")


def _render_sync_report(counts: dict, is_full: bool) -> None:
    updated_ids: list[str] = counts.get("updated_ids", [])

    if is_full:
        console.print(Panel(
            f"[bold green]{counts.get('workouts', 0)}[/bold green] workouts  ·  "
            f"[bold]{counts.get('templates', 0)}[/bold] exercise templates  ·  "
            f"[bold]{counts.get('body_measurements', 0)}[/bold] body measurements",
            title=_("sync.full_complete_title"),
            border_style="green",
        ))
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
        console.print(f"\n  {fires}  [bold]{streak}-day streak![/bold]  [dim]({freq['total_workouts']} sessions in last 4w)[/dim]")

    _render_volume_delta()
    console.print()
    _render_goals_progress()


# ── header ────────────────────────────────────────────────────────────────────

def _show_header() -> None:
    from db.store import get_sync_state
    last_sync = get_sync_state("last_sync")
    total = (query("SELECT COUNT(*) as n FROM workouts") or [{"n": 0}])[0]["n"]
    week_count = (query("SELECT COUNT(*) as n FROM workouts WHERE start_time >= datetime('now', '-7 days')") or [{"n": 0}])[0]["n"]
    freq = workout_frequency(4)
    goals = get_goals()
    name = get_pref("display_name")

    # Last workout
    lw_row = query("SELECT MAX(start_time) as t FROM workouts")
    lw_str = _("header.last_workout", ago=_time_ago(lw_row[0]["t"])) if lw_row and lw_row[0]["t"] else _("header.no_workouts")

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
            secs = int((datetime.now(timezone.utc) - datetime.fromisoformat(last_sync.replace("Z", "+00:00"))).total_seconds())
            sync_str = _("header.sync_ok", ago=_time_ago(last_sync)) if secs < 86400 else _("header.sync_stale", ago=_time_ago(last_sync))
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
    line1_parts = [lw_str] + streak_parts + [routines_str]
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

    title = f"[bold cyan]LIFTER  [dim]·[/dim]  {_esc(name)}[/bold cyan]" if name else "[bold cyan]LIFTER[/bold cyan]"
    console.print(Panel(
        f"{line1}\n{line2}\n{line3}",
        title=title,
        border_style="cyan",
        padding=(0, 2),
    ))
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
            questionary.Choice(_("sync.full"),        value="full"),
        ],
        style=STYLE,
    ).ask()
    if not sync_type:
        return

    _dlog("SYNC", "Manual sync started", type=sync_type)
    console.print()
    is_full = sync_type == "full"
    counts = full_sync(client) if is_full else incremental_sync(client)
    _render_sync_report(counts, is_full)


def _do_stats():
    default_period = get_pref("default_stats_weeks") or "8 weeks"
    weeks_str = questionary.select(
        _("stats.time_period"),
        choices=["4 weeks", "8 weeks", "12 weeks", "24 weeks"],
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
        t2.add_row(muscle, f"[cyan]{bar}[/cyan]", _fmt_weight(vol),
                   str(sets_wk.get(muscle, 0)), str(muscle_freq_data.get(muscle, 0)))
    console.print(t2)

    body = body_measurement_trend(weeks)
    if body:
        console.rule(_("stats.body_rule"))
        bt = Table(box=box.SIMPLE)
        bt.add_column(_("stats.col_metric"), style="bold")
        bt.add_column(_("stats.col_latest"), justify="right")
        bt.add_column(_("stats.col_change", weeks=weeks), justify="right")
        wt_change = body.get('weight_change_kg')
        bt.add_row(_("stats.row_weight"), _fmt_weight(body.get('weight_kg')), _fmt_weight(wt_change) if wt_change not in (None, '—') else '—')
        bt.add_row(_("stats.row_body_fat"), f"{body.get('fat_percent')}%", f"{body.get('fat_change_pct', '—')}%")
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
            pt.add_row(pr["exercise"], _fmt_weight(pr['weight_kg']), str(pr["reps"]), _fmt_weight(pr['e1rm']), pr["date"])
        console.print(pt)

    plateaus = detect_plateaus(weeks)
    if plateaus:
        console.rule(_("stats.plateaus_rule"))
        for p in plateaus:
            console.print(f"  [yellow]•[/yellow] {p['exercise']} — stalled {p['sessions_stalled']} sessions (e1RM {p['current_e1rm']} kg)")

    console.rule(_("stats.goals_rule"))
    _render_goals_progress()


def _do_progress():
    choice = questionary.select(
        _("progress.show_prompt"),
        choices=[
            questionary.Choice(_("progress.top_gainers"),       value="top"),
            questionary.Choice(_("progress.specific_exercise"), value="exercise"),
        ],
        style=STYLE,
    ).ask()
    if not choice:
        return

    weeks_str = questionary.select(
        _("progress.time_period"),
        choices=["8 weeks", "12 weeks", "24 weeks", "52 weeks"],
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
            t.add_row(g["exercise"], f"+{g['improvement_pct']}%", _fmt_weight(g['start_e1rm']), _fmt_weight(g['current_e1rm']))
        console.print(t)
    else:
        exercises = query("SELECT DISTINCT title FROM exercise_templates ORDER BY title")
        names = [e["title"] for e in exercises]
        if not names:
            console.print(_("error.no_exercises_sync_first"))
            return
        name = questionary.autocomplete(
            _("progress.search_prompt"), choices=names, style=STYLE,
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
            t.add_row(str(row["date"]), _fmt_weight(row['best_weight_kg']), str(row["best_reps"]), f"{_fmt_weight(row['e1rm'])}{change}")
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
        t.add_row(pr["exercise"], _fmt_weight(pr['weight_kg']), str(pr["reps"]), _fmt_weight(pr['e1rm']), pr["date"])
    console.print(t)


def _do_goals():
    goals = get_goals()
    action = questionary.select(
        _("goals.menu_prompt"),
        choices=[
            questionary.Choice(_("goals.view_label"),   value="view"),
            questionary.Choice(_("goals.update_label"), value="update"),
            questionary.Choice(_("goals.reset_label"),  value="reset"),
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
    elif action == "reset":
        if questionary.confirm(_("goals.clear_confirm"), default=False, style=STYLE).ask():
            clear_goals()
            _dlog("GOAL", "Goals cleared and wizard restarted")
            run_goals_wizard()


def _do_coach():
    if not _require_ai():
        return
    weeks_str = questionary.select(
        _("coach.weeks_prompt"),
        choices=["4 weeks", "8 weeks", "12 weeks", "16 weeks"],
        default="8 weeks",
        style=STYLE,
    ).ask()
    if not weeks_str:
        return
    weeks = int(weeks_str.split()[0])

    from ai.coach import get_coaching, push_routine_to_hevy

    _dlog("AI", "Coaching report requested", weeks=weeks)
    console.rule(_("coach.rule_title"))
    try:
        result = get_coaching(weeks=weeks)
    except Exception as e:
        from ai.coach import _friendly_error
        _dlog("ERROR", f"Coaching report failed: {type(e).__name__}", error=str(e)[:200])
        console.print(f"[red]{_friendly_error(e)}[/red]")
        return

    # ── scores ────────────────────────────────────────────────────────────────
    ws = result.get("workout_score")
    hs = result.get("health_score")
    cs = result.get("combined_score")
    score_items = [("Training", ws), ("Health", hs), ("Overall", cs)]
    score_items = [(lbl, v) for lbl, v in score_items if v is not None]
    if score_items:
        score_lines = [_fmt_score_bar(lbl, int(val), bar_width=16) for lbl, val in score_items]
        console.print(Panel(
            "\n".join(score_lines),
            title=_("coach.scores_panel_title"),
            border_style="cyan",
            padding=(0, 2),
        ))
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
            console.print(Panel(
                "\n".join(dist_lines),
                title=_("coach.volume_dist_title"),
                border_style="cyan",
                padding=(0, 2),
            ))

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
        console.rule(_("coach.suggested_routine_rule", title=routine.get("title")))
        console.print(f"  [dim]{routine.get('notes')}[/dim]\n")
        for ex in routine.get("exercises", []):
            if not isinstance(ex, dict):
                continue
            def _fmt_set(s: dict) -> str:
                w = s.get('weight_kg')
                w_str = _fmt_weight(w) if w else "BW"
                return f"[dim]{s.get('type', 'normal')}[/dim] {w_str}×{s.get('reps', '?')}"
            sets_str = "  ".join(_fmt_set(s) for s in ex.get("sets", []) if isinstance(s, dict))
            ex_title = ex.get("title") or ex.get("exercise_template_id", "Exercise")
            console.print(f"  [bold]{ex_title}[/bold]")
            console.print(f"    {sets_str}")
            if ex.get("notes"):
                console.print(f"    [dim italic]{ex['notes']}[/dim italic]")
        console.print()
        if questionary.confirm(_("coach.push_routine_prompt", title=routine.get("title")), default=False, style=STYLE).ask():
            client = _require_hevy()
            if client:
                try:
                    from hevy.client import _routine_id
                    from ai.coach import _show_exercise_benefits
                    resp = push_routine_to_hevy(routine)
                    console.print(_("coach.routine_pushed", routine_id=_routine_id(resp)))
                    _show_exercise_benefits(routine.get("exercises", []))
                except Exception as e:
                    console.print(f"[red]{e}[/red]")


def _do_chat():
    if not _require_ai():
        return
    weeks_str = questionary.select(
        _("chat.context_prompt"),
        choices=[
            questionary.Choice("4 weeks",              value=4),
            questionary.Choice("8 weeks",              value=8),
            questionary.Choice("12 weeks",             value=12),
            questionary.Choice(_("chat.all_time"),     value=16),
        ],
        default=8,
        style=STYLE,
    ).ask()
    if not weeks_str:
        return
    weeks = int(weeks_str)
    _dlog("AI", "Chat requested", weeks=weeks)
    from ai.coach import start_enhanced_chat
    start_enhanced_chat(weeks=weeks)


# ── settings & reset ─────────────────────────────────────────────────────────

_AI_LANGUAGES = [
    "English", "Portuguese (BR)", "Portuguese (PT)", "Spanish", "French", "German",
    "Italian", "Dutch", "Polish", "Russian", "Japanese", "Chinese",
]

_UI_LANGUAGES = [
    ("en",    "English"),
    ("pt_BR", "Português (Brasil)"),
]


def _do_ai_settings():
    from db.goals import get_pref, set_pref, get_token_usage, reset_token_usage

    while True:
        console.clear()
        usage = get_token_usage()
        total = usage["input"] + usage["output"]
        cache_pct = int(usage["cache_read"] / usage["input"] * 100) if usage["input"] else 0
        slim_on = get_pref("ai_chat_slim") != "0"
        lang = get_pref("ai_language") or "English"
        if lang == "Portuguese":
            lang = "Portuguese (BR)"
            set_pref("ai_language", lang)

        from config import AI_MODEL
        slim_label = _("settings.ai.context_slim") if slim_on else _("settings.ai.context_full")
        lines = [
            _("settings.ai.provider_line", provider=AI_PROVIDER, model=AI_MODEL),
            _("settings.ai.context_line", mode=slim_label),
            _("settings.ai.language_line", lang=lang),
            "",
            _("settings.ai.token_usage_title"),
            _("settings.ai.tokens_input", count=f"{usage['input']:,}"),
            _("settings.ai.tokens_output", count=f"{usage['output']:,}"),
            _("settings.ai.tokens_total", count=f"{total:,}"),
        ]
        if usage["cache_read"]:
            lines.append(_("settings.ai.tokens_cached", count=f"{usage['cache_read']:,}", pct=cache_pct))

        console.print(Panel("\n".join(lines), title=_("settings.ai.title"), border_style="cyan"))

        action = questionary.select(
            _("settings.ai.prompt"),
            choices=[
                questionary.Choice(
                    _("settings.ai.toggle_context_choice", mode="Slim" if slim_on else "Full"),
                    value="toggle_slim",
                ),
                questionary.Choice(_("settings.ai.language_choice", lang=lang), value="language"),
                questionary.Choice(_("settings.ai.reset_tokens_choice"),         value="reset_tokens"),
                questionary.Separator("  ───"),
                questionary.Choice(_("nav.back"),                                 value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "toggle_slim":
            new_val = "0" if slim_on else "1"
            set_pref("ai_chat_slim", new_val)
            label = _("settings.ai.context_slim") if new_val == "1" else _("settings.ai.context_full")
            _dlog("SETTING", "ai_chat_slim changed", value=label)
            console.print(_("settings.ai.context_saved", mode=label))

        elif action == "language":
            choices = _AI_LANGUAGES + ([] if lang in _AI_LANGUAGES else [lang])
            new_lang = questionary.select(
                _("settings.ai.language_prompt"),
                choices=choices,
                default=lang if lang in choices else choices[0],
                style=STYLE,
            ).ask()
            if new_lang:
                set_pref("ai_language", new_lang)
                _dlog("SETTING", "ai_language changed", value=new_lang)
                console.print(_("settings.ai.language_saved", lang=new_lang))

        elif action == "reset_tokens":
            if questionary.confirm(_("settings.ai.reset_tokens_prompt"), default=False, style=STYLE).ask():
                reset_token_usage()
                _dlog("SETTING", "token counters reset")
                console.print(_("settings.ai.reset_tokens_done"))


def _do_data_reset():
    while True:
        console.clear()
        action = questionary.select(
            _("data_reset.prompt"),
            choices=[
                questionary.Choice(_("data_reset.memories_choice"),   value="memories"),
                questionary.Choice(_("data_reset.goals_choice"),      value="goals"),
                questionary.Choice(_("data_reset.sync_state_choice"), value="sync_state"),
                questionary.Choice(_("data_reset.all_choice"),        value="all"),
                questionary.Separator("  ───"),
                questionary.Choice(_("nav.back"),                      value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "memories":
            if questionary.confirm(
                _("data_reset.memories_confirm"), default=False, style=STYLE
            ).ask():
                from db.memories import clear_memories
                clear_memories()
                _dlog("RESET", "Coach memories cleared")
                console.print(_("data_reset.memories_done"))

        elif action == "goals":
            if questionary.confirm(_("data_reset.goals_confirm"), default=False, style=STYLE).ask():
                from db.goals import clear_goals
                clear_goals()
                _dlog("RESET", "All goals cleared")
                console.print(_("data_reset.goals_done"))

        elif action == "sync_state":
            if questionary.confirm(
                _("data_reset.sync_state_confirm"), default=False, style=STYLE
            ).ask():
                from db.store import set_sync_state
                set_sync_state("last_sync", "1970-01-01T00:00:00Z")
                _dlog("RESET", "Sync state reset")
                console.print(_("data_reset.sync_state_done"))

        elif action == "all":
            console.print(_("data_reset.all_warning"))
            if not questionary.confirm(_("data_reset.all_confirm1"), default=False, style=STYLE).ask():
                continue
            if not questionary.confirm(_("data_reset.all_confirm2"), default=False, style=STYLE).ask():
                continue

            import os
            from config import DB_PATH
            try:
                from fit.auth import TOKEN_FILE, disconnect as fit_disconnect
                fit_disconnect()
            except Exception:
                pass
            try:
                os.remove(DB_PATH)
            except FileNotFoundError:
                pass

            _dlog("RESET", "Full data wipe executed")
            console.print(_("data_reset.all_done"))
            return  # DB is gone — exit all the way back to main


def _do_create_profile_flow() -> str:
    """Interactive profile creation. Returns the new slug."""
    from profile_mgr import create_profile, PROFILES_DIR
    name = (questionary.text(_("profiles.name_prompt"), style=STYLE).ask() or "").strip()
    if not name:
        name = "New Profile"
    api_key = (questionary.text(
        _("profiles.api_key_prompt"),
        style=STYLE,
    ).ask() or "").strip()
    lang_choices = [questionary.Choice(lname, value=code) for code, lname in _UI_LANGUAGES]
    lang_code = questionary.select(
        _("profiles.language_prompt"),
        choices=lang_choices,
        style=STYLE,
    ).ask() or "en"
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
        list_profiles, get_active_slug, activate_profile, set_active_slug,
        rename_profile, delete_profile, get_profile_name,
    )

    while True:
        console.clear()
        active_slug = get_active_slug()
        active_name = get_profile_name(active_slug) if active_slug else "None"
        profiles = list_profiles()

        console.print(Panel(
            _("profiles.panel_content", name=_esc(active_name), total=len(profiles)),
            title=_("profiles.panel_title"),
            border_style="cyan",
            padding=(0, 2),
        ))

        action = questionary.select(
            _("profiles.menu_prompt"),
            choices=[
                questionary.Choice(_("profiles.switch_choice"), value="switch"),
                questionary.Choice(_("profiles.create_choice"), value="create"),
                questionary.Choice(_("profiles.rename_choice"), value="rename"),
                questionary.Choice(_("profiles.delete_choice"), value="delete"),
                questionary.Separator("  ───────────────────────────────────────"),
                questionary.Choice(_("nav.back"),               value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "switch":
            if len(profiles) <= 1:
                console.print(_("profiles.only_one"))
                questionary.press_any_key_to_continue(style=STYLE).ask()
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
                _os.execv(_sys.executable, [_sys.executable] + _sys.argv)

        elif action == "create":
            slug = _do_create_profile_flow()
            if questionary.confirm(_("profiles.switch_now"), default=True, style=STYLE).ask():
                _dlog("PROFILE", "Switched to newly created profile", slug=slug)
                set_active_slug(slug)
                import os as _os
                import sys as _sys
                _os.execv(_sys.executable, [_sys.executable] + _sys.argv)

        elif action == "rename":
            if active_slug:
                new_name = (questionary.text(
                    _("profiles.rename_prompt", name=_esc(active_name)),
                    style=STYLE,
                ).ask() or "").strip()
                if new_name:
                    rename_profile(active_slug, new_name)
                    console.print(_("profiles.renamed", name=_esc(new_name)))

        elif action == "delete":
            others = [p for p in profiles if p["slug"] != active_slug]
            if not others:
                console.print(_("profiles.cannot_delete_only"))
                questionary.press_any_key_to_continue(style=STYLE).ask()
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
    from profile_mgr import get_active_slug, update_profile_key, PROFILES_DIR
    import json as _json

    active_slug = get_active_slug()
    name = get_pref("display_name") or ""

    hevy_key = ""
    if active_slug:
        cfg_file = PROFILES_DIR / active_slug / "profile.json"
        if cfg_file.exists():
            try:
                hevy_key = _json.loads(cfg_file.read_text()).get("hevy_api_key", "")
            except Exception:
                pass
    masked_key = (hevy_key[:4] + "…" + hevy_key[-4:]) if len(hevy_key) > 8 else ("set" if hevy_key else "not set")

    name_line = _("profile.display_name_label", name=_esc(name)) if name else _("profile.display_name_notset")
    console.print(Panel(
        f"{name_line}\n{_('profile.api_key_label', key=masked_key)}",
        title=_("profile.panel_title"),
        border_style="cyan",
        padding=(0, 2),
    ))

    action = questionary.select(
        _("profile.edit_prompt"),
        choices=[
            questionary.Choice(_("profile.display_name_choice"), value="name"),
            questionary.Choice(_("profile.api_key_choice"),      value="apikey"),
            questionary.Choice(_("nav.cancel"),                  value="back"),
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


def _do_preferences_settings() -> None:
    import debug_log
    import i18n as _i18n
    while True:
        console.clear()
        units = _get_units()
        checkin_days = int(get_pref("goals_checkin_days") or 7)
        auto_sync = get_pref("auto_sync") == "1"
        default_weeks = get_pref("default_stats_weeks") or "8 weeks"
        debug_on = get_pref("debug_logging") == "1"
        ui_lang_code = get_pref("ui_language") or config.DEFAULT_LANGUAGE
        ui_lang_name = dict(_UI_LANGUAGES).get(ui_lang_code, ui_lang_code)
        on_str = _("settings.on")
        off_str = _("settings.off")

        lines = [
            _("settings.prefs.units_label", units=units),
            _("settings.prefs.checkin_label", days=checkin_days),
            _("settings.prefs.autosync_label", state=on_str if auto_sync else off_str),
            _("settings.prefs.stats_window_label", window=default_weeks),
            _("settings.prefs.debug_label", state=on_str if debug_on else off_str),
            _("settings.prefs.ui_language_label", lang=ui_lang_name),
        ]
        console.print(Panel("\n".join(lines), title=_("settings.prefs.title"), border_style="cyan", padding=(0, 2)))

        action = questionary.select(
            _("settings.prefs.prompt"),
            choices=[
                questionary.Choice(_("settings.prefs.units_choice", units=units),                            value="units"),
                questionary.Choice(_("settings.prefs.checkin_choice", days=checkin_days),                    value="checkin"),
                questionary.Choice(_("settings.prefs.autosync_choice", state=on_str if auto_sync else off_str), value="autosync"),
                questionary.Choice(_("settings.prefs.stats_window_choice", window=default_weeks),            value="stats_window"),
                questionary.Choice(_("settings.prefs.debug_choice", state=on_str if debug_on else off_str),  value="debug"),
                questionary.Choice(_("settings.prefs.ui_language_choice", lang=ui_lang_name),                value="ui_language"),
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
                    questionary.Choice(_("settings.prefs.units_kg"),  value="kg"),
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
                    questionary.Choice(_("settings.prefs.checkin_7"),  value="7"),
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

        elif action == "stats_window":
            new_window = questionary.select(
                _("settings.prefs.stats_window_prompt"),
                choices=["4 weeks", "8 weeks", "12 weeks", "24 weeks"],
                default=default_weeks,
                style=STYLE,
            ).ask()
            if new_window:
                set_pref("default_stats_weeks", new_window)
                _dlog("SETTING", "default_stats_weeks changed", value=new_window)
                console.print(_("settings.prefs.stats_window_saved", window=new_window))

        elif action == "debug":
            new_val = not debug_on
            set_pref("debug_logging", "1" if new_val else "0")
            debug_log.enable(new_val)
            _dlog("SETTING", "debug_logging changed", value="on" if new_val else "off")
            if new_val:
                from debug_log import LOGS_DIR
                console.print(_("settings.prefs.debug_enabled", logs_dir=LOGS_DIR))
            else:
                console.print(_("settings.prefs.debug_disabled"))

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


def _do_settings() -> None:
    while True:
        console.clear()
        action = questionary.select(
            _("settings.menu_prompt"),
            choices=[
                questionary.Choice(_("settings.profiles_choice"), value="profiles"),
                questionary.Choice(_("settings.profile_choice"),  value="profile"),
                questionary.Choice(_("settings.prefs_choice"),    value="prefs"),
                questionary.Choice(_("settings.ai_choice"),       value="ai"),
                questionary.Separator("  ───────────────────────────────────────"),
                questionary.Choice(_("settings.reset_choice"),    value="reset"),
                questionary.Separator("  ───────────────────────────────────────"),
                questionary.Choice(_("nav.back"),                 value="back"),
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
        elif action == "reset":
            _do_data_reset()


# ── google fit ────────────────────────────────────────────────────────────────

def _render_recovery_panel() -> None:
    """Show a compact recovery panel if Fit data exists."""
    try:
        from fit.analytics import recovery_score, sleep_summary, activity_summary
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
            parts.append(f"Recovery [{rec['color']}]{rec['score']}/100 {rec['label']}[/{rec['color']}]")
        if sleep.get("avg_hours"):
            parts.append(f"Sleep {sleep['avg_hours']}h avg")
        if activity.get("avg_steps"):
            parts.append(f"Steps {int(activity['avg_steps']):,}/day")
        if activity.get("resting_hr"):
            parts.append(f"RHR {activity['resting_hr']} bpm")
        if parts:
            console.print(Panel("  ·  ".join(parts), title="[bold green]Recovery[/bold green]",
                                border_style="green", padding=(0, 2)))
            console.print()
    except Exception:
        pass


def _do_fit():
    from fit.auth import is_connected, disconnect

    action = questionary.select(
        _("fit.menu_prompt"),
        choices=[
            questionary.Choice(_("fit.sync_choice"),       value="sync"),
            questionary.Choice(_("fit.connect_choice"),    value="connect"),
            questionary.Choice(_("fit.view_choice"),       value="view"),
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
            choices=["7 days", "14 days", "30 days", "90 days"],
            default="30 days",
            style=STYLE,
        ).ask()
        if not days_str:
            return
        days = int(days_str.split()[0])
        console.print(_("fit.syncing_n_days", days=days))
        try:
            from fit.sync import sync_fit
            counts = sync_fit(days=days)
            console.print(Panel(
                f"[bold green]{counts['daily_days']}[/bold green] daily records  ·  "
                f"[bold green]{counts['sleep_sessions']}[/bold green] sleep sessions",
                title=_("fit.sync_complete_title"),
                border_style="green",
            ))
            _render_recovery_panel()
        except Exception as e:
            _dlog("ERROR", f"Google Fit sync failed: {type(e).__name__}", error=str(e)[:200])
            console.print(_("error.fit_sync_failed", error=e))

    elif action == "view":
        if not is_connected():
            console.print(_("fit.not_connected_short"))
            return
        _render_fit_dashboard()

    elif action == "disconnect":
        if questionary.confirm(_("fit.disconnect_confirm"), default=False, style=STYLE).ask():
            disconnect()
            _dlog("SETTING", "Google Fit disconnected")
            console.print(_("fit.disconnected"))


def _fit_setup() -> None:
    console.rule(_("fit.connect_rule"))
    console.print("""
  [bold]Step 1[/bold] — Create OAuth credentials in Google Cloud Console:

    1. Go to [link]https://console.cloud.google.com[/link]
    2. Create a new project (or select an existing one)
    3. Go to [bold]APIs & Services → Library[/bold] and enable [bold]Fitness API[/bold]
    4. Go to [bold]APIs & Services → OAuth consent screen[/bold]
       → External → fill in app name → add your Gmail as a test user
    5. Go to [bold]APIs & Services → Credentials → Create Credentials → OAuth client ID[/bold]
       → Application type: [bold]Desktop app[/bold]
    6. Download the JSON file and save it as [bold]fit_credentials.json[/bold]
       in this project folder

  [bold]Step 2[/bold] — The browser will open for you to approve access.
""")

    if not questionary.confirm(_("fit.ready_to_auth"), default=True, style=STYLE).ask():
        return

    try:
        from fit.auth import get_credentials, CREDENTIALS_FILE
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
    from fit.analytics import sleep_summary, activity_summary, recovery_score

    console.rule(_("fit.dashboard_rule"))

    rec = recovery_score(3)
    if rec:
        score = rec["score"]
        bar_w = int(score / 100 * 30)
        bar = f"[{rec['color']}]{'█' * bar_w}[/{rec['color']}][dim]{'░' * (30 - bar_w)}[/dim]"
        console.print(f"\n  Recovery Score  {bar}  [{rec['color']}]{score}/100  {rec['label']}[/{rec['color']}]\n")

    for label, days in [("Last 7 days", 7), ("Last 14 days", 14)]:
        sleep = sleep_summary(days)
        activity = activity_summary(days)
        if not sleep and not activity:
            continue
        console.rule(f"[dim]{label}[/dim]")
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
            return False
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - dt).total_seconds() > 86400
        except Exception:
            return False

    stale_hevy = _is_stale("last_sync")

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
        elif questionary.confirm(
            _("sync.stale_hevy_prompt"), default=True, style=STYLE
        ).ask():
            _dlog("SYNC", "User accepted Hevy sync prompt")
            client = _require_hevy()
            if client:
                counts = incremental_sync(client)
                console.print(_("sync.hevy_done", updated=counts["updated"], deleted=counts["deleted"]))
        else:
            _dlog("SYNC", "User declined Hevy sync prompt")

    if stale_fit:
        if auto_sync:
            try:
                _dlog("SYNC", "Google Fit auto-sync triggered (data stale >24h)")
                console.print(_("sync.auto_syncing_fit"))
                from fit.sync import sync_fit
                counts = sync_fit(days=30)
                console.print(_("sync.auto_synced_fit", daily_days=counts["daily_days"], sleep_sessions=counts["sleep_sessions"]))
            except Exception as e:
                _dlog("SYNC", "Google Fit auto-sync error", error=str(e)[:200])
                console.print(_("sync.auto_sync_fit_failed", error=e))
        elif questionary.confirm(
            _("sync.stale_fit_prompt"), default=True, style=STYLE
        ).ask():
            _dlog("SYNC", "User accepted Google Fit sync prompt")
            try:
                from fit.sync import sync_fit
                counts = sync_fit(days=90)
                console.print(_("sync.fit_done", daily_days=counts["daily_days"], sleep_sessions=counts["sleep_sessions"]))
            except Exception as e:
                console.print(_("error.fit_sync_failed", error=e))
        else:
            _dlog("SYNC", "User declined Google Fit sync prompt")


def _check_goals_and_checkin() -> None:
    if should_ask_goals():
        goals = get_goals()
        if not goals:
            # First time ever
            if questionary.confirm(
                _("goals.set_now_first_run"), default=True, style=STYLE
            ).ask():
                _dlog("GOAL", "First-run goals wizard started")
                run_goals_wizard()
            else:
                _dlog("GOAL", "First-run goals wizard declined")
        else:
            # Weekly check-in
            _dlog("GOAL", "Weekly check-in triggered")
            _weekly_checkin()


# ── main loop ─────────────────────────────────────────────────────────────────

ACTIONS = {
    "sync":     _do_sync,
    "stats":    _do_stats,
    "progress": _do_progress,
    "records":  _do_records,
    "goals":    _do_goals,
    "fit":      _do_fit,
    "coach":    _do_coach,
    "chat":     _do_chat,
    "settings": _do_settings,
}

_NO_PAUSE = {"chat"}


def _build_menu() -> tuple:
    try:
        from fit.auth import is_connected as _fit_connected
        fit_label = _("menu.fit_connected") if _fit_connected() else _("menu.fit_disconnected")
    except Exception:
        fit_label = _("menu.fit_connected")

    last_action = get_pref("last_menu_action")
    items = [
        questionary.Choice(_("menu.sync"),     value="sync"),
        questionary.Choice(_("menu.chat"),     value="chat"),
        questionary.Separator("  ──────────────────────────────────"),
        questionary.Choice(_("menu.goals"),    value="goals"),
        questionary.Choice(_("menu.stats"),    value="stats"),
        questionary.Choice(_("menu.progress"), value="progress"),
        questionary.Choice(_("menu.records"),  value="records"),
        questionary.Separator("  ──────────────────────────────────"),
        questionary.Choice(_("menu.coach"),    value="coach"),
        questionary.Choice(fit_label,          value="fit"),
        questionary.Separator("  ──────────────────────────────────"),
        questionary.Choice(_("menu.settings"), value="settings"),
        questionary.Choice(_("menu.exit"),     value="exit"),
    ]
    default = next((c for c in items if isinstance(c, questionary.Choice) and c.value == last_action), None)
    return items, default


def _bootstrap_profiles() -> None:
    """Select or create a profile before any DB operations."""
    import shutil as _shutil
    from pathlib import Path as _Path
    from profile_mgr import (
        PROFILES_FILE, PROFILES_DIR, list_profiles, get_active_slug,
        set_active_slug, activate_profile, create_profile,
    )

    project_dir = _Path(__file__).resolve().parent
    old_db    = project_dir / "hevy.db"
    old_token = project_dir / "fit_token.json"

    # Migration: existing single-user hevy.db with no profiles.json yet
    if not PROFILES_FILE.exists() and old_db.exists():
        console.print()
        console.print(Panel(
            _("migration.panel_body"),
            title=_("migration.panel_title"),
            border_style="cyan",
            padding=(0, 2),
        ))
        name = (questionary.text(
            _("migration.name_prompt"),
            default="Default",
            style=STYLE,
        ).ask() or "Default").strip()

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
        name = (questionary.text(
            _("welcome.name_prompt"),
            default="Athlete",
            style=STYLE,
        ).ask() or "Athlete").strip()
        api_key = (questionary.text(
            _("welcome.api_key_prompt"),
            style=STYLE,
        ).ask() or "").strip()
        lang_choices = [questionary.Choice(lname, value=code) for code, lname in _UI_LANGUAGES]
        lang_code = questionary.select(
            _("welcome.language_prompt"),
            choices=lang_choices,
            style=STYLE,
        ).ask() or "en"
        profile = create_profile(name, hevy_api_key=api_key)
        activate_profile(profile["slug"])
        init_db()
        set_pref("ui_language", lang_code)
        import i18n as _i18n
        _i18n.init(lang_code)
        _dlog("PROFILE", "First run: profile created", slug=profile["slug"])
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
    import i18n as _i18n
    _i18n.init(config.DEFAULT_LANGUAGE)   # Phase 1: before profile selector
    _bootstrap_profiles()
    init_db()
    ui_lang = get_pref("ui_language") or config.DEFAULT_LANGUAGE
    _i18n.init(ui_lang)                   # Phase 2: after profile DB is open
    import debug_log
    debug_log.init()
    from profile_mgr import get_active_slug
    import config as _cfg
    _dlog("APP", "Lifter started",
          profile=get_active_slug() or "none",
          provider=_cfg.AI_PROVIDER, model=_cfg.AI_MODEL)
    _check_goals_and_checkin()
    _check_stale_sync()

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
        if action:
            action()

        if choice not in _NO_PAUSE:
            _pause()


if __name__ == "__main__":
    main()
