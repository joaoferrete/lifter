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
        console.print("[red]Hevy API key not set. Go to Settings → Profiles to add it.[/red]")
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
        console.print(f"[red]{var} not set in .env (AI_PROVIDER={AI_PROVIDER})[/red]")
        return False
    return True


def _pause():
    console.print()
    questionary.press_any_key_to_continue("  Press any key to return to menu...").ask()


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
        lines.append("[bold dim]Last report scores[/bold dim]")
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
        lines.append("[bold dim]Volume split (4w)[/bold dim]")
        lines.append("  ".join(dist_parts))
        lines.append("")

    # Compact goal progress
    from db.goals import compute_goal_progress
    progress = compute_goal_progress()
    numeric = [g for g in progress if g.get("pct") is not None and not g["achieved"]]
    achieved = [g for g in progress if g["achieved"]]
    if numeric or achieved:
        lines.append("[bold dim]Goals[/bold dim]")
        for g in numeric[:4]:
            pct = float(g["pct"])
            color = _score_color(int(pct))
            bw = max(1, int(pct / 100 * 8))
            bar = f"[{color}]{'█' * bw}[/{color}][dim]{'░' * (8 - bw)}[/dim]"
            desc = g["description"][:30]
            lines.append(f"  {bar} [{color}]{pct:.0f}%[/{color}]  [dim]{desc}[/dim]")
        if len(numeric) > 4:
            lines.append(f"  [dim]...and {len(numeric) - 4} more goal(s)[/dim]")
        for g in achieved[:2]:
            lines.append(f"  [bold green]✓[/bold green] [dim]{g['description'][:35]}[/dim]")
        custom = [g for g in progress if g.get("pct") is None and not g["achieved"]]
        for g in custom[:2]:
            lines.append(f"  [dim]◦ {g['description'][:35]} (custom)[/dim]")

    if not lines:
        return

    console.print(Panel(
        "\n".join(lines).strip(),
        title="[bold dim]Quick view[/bold dim]",
        border_style="dim",
        padding=(0, 2),
    ))
    console.print()


# ── goals wizard ──────────────────────────────────────────────────────────────

def _wizard_lift_prs() -> None:
    exercises = query("SELECT id, title FROM exercise_templates ORDER BY title")
    if not exercises:
        console.print("[yellow]  No exercises found. Run Sync first.[/yellow]")
        return
    names = [e["title"] for e in exercises]
    id_by_name = {e["title"]: e["id"] for e in exercises}
    units = _get_units()

    console.print("\n  [dim]Add one or more lift targets. Leave blank to stop.[/dim]")

    while True:
        name = questionary.autocomplete(
            "  Exercise (start typing or press Enter to stop):",
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
            f"  Target weight in {units}? (your current e1RM: {current_display})",
            style=STYLE,
            validate=lambda v: (v == "" or v.replace(".", "").isdigit()) or "Enter a number",
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
        console.print(f"  [green]✓[/green] Goal saved: {name} {target_label}\n")

        if not questionary.confirm("  Add another lift goal?", default=False, style=STYLE).ask():
            break


def _wizard_frequency() -> None:
    choice = questionary.select(
        "  Target sessions per week:",
        choices=["2", "3", "4", "5", "6"],
        default="4",
        style=STYLE,
    ).ask()
    if choice:
        target = int(choice)
        save_goal(type="frequency", description=f"Train {target}× per week", target=target, unit="sessions/wk")
        console.print(f"  [green]✓[/green] Goal saved: Train {target}× per week\n")


def _wizard_weight(goal_type: str) -> None:
    rows = query("SELECT weight_kg FROM body_measurements WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT 1")
    current_kg = rows[0]["weight_kg"] if rows else None
    units = _get_units()
    hint = f" (current: {_fmt_weight(current_kg)})" if current_kg else ""

    target_str = questionary.text(
        f"  Target body weight in {units}{hint}:",
        style=STYLE,
        validate=lambda v: v.replace(".", "").isdigit() or "Enter a number",
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
    console.print(f"  [green]✓[/green] Goal saved: {direction} to {target_label}\n")


def _wizard_body_fat() -> None:
    rows = query("SELECT fat_percent FROM body_measurements WHERE fat_percent IS NOT NULL ORDER BY date DESC LIMIT 1")
    current = rows[0]["fat_percent"] if rows else None
    hint = f" (current: {current}%)" if current else ""

    target_str = questionary.text(
        f"  Target body fat %{hint}:",
        style=STYLE,
        validate=lambda v: v.replace(".", "").isdigit() or "Enter a number",
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
    console.print(f"  [green]✓[/green] Goal saved: Reach {target}% body fat\n")


def _wizard_volume() -> None:
    muscles = [
        "chest", "lats", "upper_back", "shoulders", "biceps", "triceps",
        "quadriceps", "hamstrings", "glutes", "calves", "abdominals",
    ]
    muscle = questionary.select("  Which muscle group?", choices=muscles, style=STYLE).ask()
    if not muscle:
        return
    target_str = questionary.text(
        f"  Target sets per week for {muscle}:",
        style=STYLE,
        validate=lambda v: v.isdigit() or "Enter a whole number",
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
    console.print("  [green]✓[/green] Goal saved\n")


def _wizard_custom() -> None:
    text = questionary.text(
        "  Describe your goal:",
        style=STYLE,
        validate=lambda v: len(v.strip()) > 3 or "Please describe your goal",
    ).ask()
    if text:
        save_goal(type="custom", description=text.strip())
        console.print("  [green]✓[/green] Goal saved\n")


def run_goals_wizard(is_update: bool = False) -> None:
    name = get_pref("display_name")
    if not name:
        console.print()
        name = questionary.text("  What's your name?", style=STYLE).ask()
        if name:
            set_pref("display_name", name.strip())

    greet = f"Let's update your goals, {name}!" if is_update else f"Welcome, {name}! Let's set your training goals."
    console.print(f"\n  [bold cyan]{greet}[/bold cyan]\n")

    selected = questionary.checkbox(
        "  What are you training for? (use Space to select, Enter to confirm)",
        choices=[
            questionary.Choice("Build strength — hit a specific lift target", value="lift_pr"),
            questionary.Choice("Train consistently — hit X sessions per week", value="frequency"),
            questionary.Choice("Lose body weight", value="weight_loss"),
            questionary.Choice("Gain body weight / muscle mass", value="weight_gain"),
            questionary.Choice("Reduce body fat %", value="body_fat"),
            questionary.Choice("Increase weekly volume for a muscle group", value="volume"),
            questionary.Choice("Other — free text goal", value="custom"),
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
    console.print(f"\n  [bold green]✓ {total} goal(s) saved.[/bold green] The AI coach will now track your progress.\n")


def _weekly_checkin() -> None:
    goals = get_goals()
    if not goals:
        console.print()
        run_goals_wizard()
        return

    name = get_pref("display_name") or "there"
    console.print(f"\n  [bold cyan]Weekly goals check-in, {_esc(name)}![/bold cyan]\n")
    console.print("  Your current goals:\n")
    for g in goals:
        console.print(f"    [dim]•[/dim] {_esc(g['description'])}")
    console.print()

    answer = questionary.select(
        "  Are these goals still the same?",
        choices=[
            questionary.Choice("Yes, keep them", value="keep"),
            questionary.Choice("Update my goals", value="update"),
            questionary.Choice("Skip for now", value="skip"),
        ],
        style=STYLE,
    ).ask()

    if answer == "update":
        run_goals_wizard(is_update=True)
    elif answer == "keep":
        mark_goals_asked()
        console.print("  [dim]Goals confirmed. See you next week![/dim]\n")
    # skip: don't update the timestamp so we ask again next run


# ── goal progress rendering ───────────────────────────────────────────────────

def _render_goals_progress() -> None:
    progress = compute_goal_progress()
    if not progress:
        return

    lines = []
    for g in progress:
        if g["achieved"]:
            lines.append(f"  [bold green]✓ ACHIEVED[/bold green]  [bold]{g['description']}[/bold]  🎉")
            lines.append("")
            continue

        pct = g.get("pct")
        if pct is None:
            lines.append(f"  [dim]◦[/dim] [bold]{g['description']}[/bold]  [dim](custom goal)[/dim]")
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
            title="[bold yellow]Goals Progress[/bold yellow]",
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
    console.print("\n  [bold]Volume this week vs last week[/bold]")
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
            title="[bold green]Full sync complete[/bold green]",
            border_style="green",
        ))
    else:
        updated = counts.get("updated", 0)
        deleted = counts.get("deleted", 0)
        if updated == 0 and deleted == 0:
            console.print(Panel("[dim]Already up to date.[/dim]", border_style="green"))
            _render_goals_progress()
            return
        parts = []
        if updated:
            parts.append(f"[bold green]{updated}[/bold green] new/updated")
        if deleted:
            parts.append(f"[bold red]{deleted}[/bold red] deleted")
        console.print(Panel(" · ".join(parts), title="[bold green]Sync complete[/bold green]", border_style="green"))

    if updated_ids:
        _render_workout_cards(updated_ids[:4])
        if len(updated_ids) > 4:
            console.print(f"  [dim]...and {len(updated_ids) - 4} more[/dim]")

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
    lw_str = f"Last workout: [bold]{_time_ago(lw_row[0]['t'])}[/bold]" if lw_row and lw_row[0]["t"] else "[dim]No workouts yet[/dim]"

    # Streak
    streak = freq.get("longest_streak_days", 0)
    streak_parts = []
    if streak >= 2:
        fires = "🔥" * min(streak, 5)
        streak_parts.append(f"{fires} [bold]{streak}d streak[/bold]")

    # Routines count
    routine_count = (query("SELECT COUNT(*) as n FROM routines") or [{"n": 0}])[0]["n"]
    routines_str = f"[bold]{routine_count}[/bold] routine{'s' if routine_count != 1 else ''}"

    # Sync status
    if last_sync:
        try:
            secs = int((datetime.now(timezone.utc) - datetime.fromisoformat(last_sync.replace("Z", "+00:00"))).total_seconds())
            sync_str = f"Sync [green]✓[/green] {_time_ago(last_sync)}" if secs < 86400 else f"Sync [yellow]⚠[/yellow] {_time_ago(last_sync)}"
        except Exception:
            sync_str = "Sync [dim]?[/dim]"
    else:
        sync_str = "Sync [dim]never[/dim]"

    # AI provider
    from ai.provider import provider_label
    ai_str = f"AI: {provider_label()}"

    # Recovery from Google Fit
    recovery_str = ""
    try:
        from fit.auth import is_connected
        if is_connected():
            from fit.analytics import recovery_score
            rec = recovery_score(3)
            if rec:
                recovery_str = f"  ·  Recovery [{rec['color']}]{rec['score']}/100[/{rec['color']}]"
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
        line2 += f"  ·  [yellow]{len(goals)} goal{'s' if len(goals) != 1 else ''}[/yellow]"

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
        "Sync type:",
        choices=[
            questionary.Choice("Incremental  (only fetch what's new)", value="inc"),
            questionary.Choice("Full  (re-download everything)", value="full"),
        ],
        style=STYLE,
    ).ask()
    if not sync_type:
        return

    console.print()
    is_full = sync_type == "full"
    counts = full_sync(client) if is_full else incremental_sync(client)
    _render_sync_report(counts, is_full)


def _do_stats():
    default_period = get_pref("default_stats_weeks") or "8 weeks"
    weeks_str = questionary.select(
        "Time period:",
        choices=["4 weeks", "8 weeks", "12 weeks", "24 weeks"],
        default=default_period,
        style=STYLE,
    ).ask()
    if not weeks_str:
        return
    weeks = int(weeks_str.split()[0])

    freq = workout_frequency(weeks)
    if freq["total_workouts"] == 0:
        console.print("[yellow]No data. Run Sync first.[/yellow]")
        return

    console.rule(f"[bold cyan]Training Stats — last {weeks} weeks[/bold cyan]")

    t = Table(box=box.SIMPLE)
    t.add_column("Metric", style="bold")
    t.add_column("Value", justify="right")
    t.add_row("Total workouts", str(freq["total_workouts"]))
    t.add_row("Avg workouts / week", str(freq["avg_per_week"]))
    t.add_row("Avg session duration", f"{freq['avg_duration_minutes']} min")
    t.add_row("Avg rest between sessions", f"{freq['rest_day_avg']} days")
    t.add_row("Longest streak", f"{freq['longest_streak_days']} days")
    console.print(t)

    console.rule("[bold]Volume by muscle group[/bold]")
    muscle_vol = muscle_group_summary(weeks)
    sets_wk = sets_per_muscle_per_week(weeks)
    muscle_freq_data = muscle_group_frequency(weeks)
    max_vol = max(muscle_vol.values()) if muscle_vol else 1.0

    t2 = Table(box=box.SIMPLE)
    t2.add_column("Muscle", style="bold")
    t2.add_column("Volume", no_wrap=True)
    t2.add_column("Tonnage", justify="right")
    t2.add_column("Sets/wk", justify="right")
    t2.add_column("Sessions/wk", justify="right")
    for muscle, vol in muscle_vol.items():
        bar_w = max(1, int(vol / max_vol * 16))
        bar = "█" * bar_w + "░" * (16 - bar_w)
        t2.add_row(muscle, f"[cyan]{bar}[/cyan]", _fmt_weight(vol),
                   str(sets_wk.get(muscle, 0)), str(muscle_freq_data.get(muscle, 0)))
    console.print(t2)

    body = body_measurement_trend(weeks)
    if body:
        console.rule("[bold]Body measurements[/bold]")
        bt = Table(box=box.SIMPLE)
        bt.add_column("Metric", style="bold")
        bt.add_column("Latest", justify="right")
        bt.add_column(f"Change ({weeks}w)", justify="right")
        wt_change = body.get('weight_change_kg')
        bt.add_row("Weight", _fmt_weight(body.get('weight_kg')), _fmt_weight(wt_change) if wt_change not in (None, '—') else '—')
        bt.add_row("Body fat", f"{body.get('fat_percent')}%", f"{body.get('fat_change_pct', '—')}%")
        console.print(bt)

    prs = recent_prs(30)
    if prs:
        console.rule("[bold]Personal records — last 30 days[/bold]")
        pt = Table(box=box.SIMPLE)
        pt.add_column("Exercise", style="bold")
        pt.add_column("Weight", justify="right")
        pt.add_column("Reps", justify="right")
        pt.add_column("e1RM", justify="right")
        pt.add_column("Date")
        for pr in prs:
            pt.add_row(pr["exercise"], _fmt_weight(pr['weight_kg']), str(pr["reps"]), _fmt_weight(pr['e1rm']), pr["date"])
        console.print(pt)

    plateaus = detect_plateaus(weeks)
    if plateaus:
        console.rule("[bold yellow]Plateaus[/bold yellow]")
        for p in plateaus:
            console.print(f"  [yellow]•[/yellow] {p['exercise']} — stalled {p['sessions_stalled']} sessions (e1RM {p['current_e1rm']} kg)")

    console.rule("[bold yellow]Goals Progress[/bold yellow]")
    _render_goals_progress()


def _do_progress():
    choice = questionary.select(
        "What to show?",
        choices=[
            questionary.Choice("Top gainers", value="top"),
            questionary.Choice("Specific exercise", value="exercise"),
        ],
        style=STYLE,
    ).ask()
    if not choice:
        return

    weeks_str = questionary.select(
        "Time period:",
        choices=["8 weeks", "12 weeks", "24 weeks", "52 weeks"],
        default="12 weeks",
        style=STYLE,
    ).ask()
    if not weeks_str:
        return
    weeks = int(weeks_str.split()[0])

    if choice == "top":
        console.rule(f"[bold]Top progressions — last {weeks} weeks[/bold]")
        top = top_progressions(weeks, top_n=10)
        if not top:
            console.print("[yellow]Not enough data yet.[/yellow]")
            return
        t = Table(box=box.SIMPLE)
        t.add_column("Exercise", style="bold")
        t.add_column("Improvement", justify="right")
        t.add_column("Start e1RM", justify="right")
        t.add_column("Current e1RM", justify="right")
        for g in top:
            t.add_row(g["exercise"], f"+{g['improvement_pct']}%", _fmt_weight(g['start_e1rm']), _fmt_weight(g['current_e1rm']))
        console.print(t)
    else:
        exercises = query("SELECT DISTINCT title FROM exercise_templates ORDER BY title")
        names = [e["title"] for e in exercises]
        if not names:
            console.print("[yellow]No exercises found. Run Sync first.[/yellow]")
            return
        name = questionary.autocomplete(
            "Search exercise:", choices=names, style=STYLE,
            validate=lambda v: v in names or "Pick from the list",
        ).ask()
        if not name:
            return
        rows = query("SELECT id FROM exercise_templates WHERE title = ?", (name,))
        if not rows:
            return
        console.rule(f"[bold]{name}[/bold]")
        df = exercise_progression(rows[0]["id"], weeks)
        if df.empty:
            console.print("[yellow]No progression data.[/yellow]")
            return
        t = Table(box=box.SIMPLE)
        t.add_column("Date")
        t.add_column("Best weight", justify="right")
        t.add_column("Reps", justify="right")
        t.add_column("e1RM", justify="right")
        prev_e1rm = None
        for _, row in df.iterrows():
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
    prs = all_time_records()
    if not prs:
        console.print("[yellow]No records yet. Run Sync first.[/yellow]")
        return
    console.rule("[bold]All-time personal records[/bold]")
    t = Table(box=box.SIMPLE)
    t.add_column("Exercise", style="bold")
    t.add_column("Weight", justify="right")
    t.add_column("Reps", justify="right")
    t.add_column("e1RM", justify="right")
    t.add_column("Date")
    for pr in prs:
        t.add_row(pr["exercise"], _fmt_weight(pr['weight_kg']), str(pr["reps"]), _fmt_weight(pr['e1rm']), pr["date"])
    console.print(t)


def _do_goals():
    goals = get_goals()
    action = questionary.select(
        "Goals:",
        choices=[
            questionary.Choice("View current goals & progress", value="view"),
            questionary.Choice("Update my goals", value="update"),
            questionary.Choice("Set goals from scratch", value="reset"),
        ],
        style=STYLE,
    ).ask()
    if not action:
        return
    if action == "view":
        if not goals:
            console.print("[yellow]No goals set yet.[/yellow]")
            if questionary.confirm("  Set goals now?", default=True, style=STYLE).ask():
                run_goals_wizard()
        else:
            _render_goals_progress()
    elif action == "update":
        run_goals_wizard(is_update=True)
    elif action == "reset":
        if questionary.confirm("  Clear all goals and start fresh?", default=False, style=STYLE).ask():
            clear_goals()
            run_goals_wizard()


def _do_coach():
    if not _require_ai():
        return
    weeks_str = questionary.select(
        "Weeks to analyse:",
        choices=["4 weeks", "8 weeks", "12 weeks", "16 weeks"],
        default="8 weeks",
        style=STYLE,
    ).ask()
    if not weeks_str:
        return
    weeks = int(weeks_str.split()[0])

    from ai.coach import get_coaching, push_routine_to_hevy

    console.rule("[bold cyan]AI Coaching Report[/bold cyan]")
    try:
        result = get_coaching(weeks=weeks)
    except Exception as e:
        from ai.coach import _friendly_error
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
            title="[bold]Performance Scores[/bold]",
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
                title="[bold]Volume Distribution[/bold]",
                border_style="cyan",
                padding=(0, 2),
            ))

    # ── analysis ──────────────────────────────────────────────────────────────
    console.rule("[bold green]Strengths[/bold green]")
    for s in result.get("strengths", []):
        console.print(f"  [green]✓[/green] {s}")

    console.rule("[bold yellow]Areas to improve[/bold yellow]")
    for w in result.get("weaknesses", []):
        console.print(f"  [yellow]![/yellow] {w}")

    console.rule("[bold]Recommendations[/bold]")
    for r in result.get("recommendations", []):
        console.print(f"  [cyan]→[/cyan] {r}")

    console.rule("[bold]Next focus[/bold]")
    console.print(f"  {result.get('next_focus', '')}")

    # ── suggested routine ─────────────────────────────────────────────────────
    routine = result.get("routine", {})
    if routine:
        console.rule(f"[bold]Suggested routine: {routine.get('title')}[/bold]")
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
        if questionary.confirm(f"  Push '{routine.get('title')}' to your Hevy app?", default=False, style=STYLE).ask():
            client = _require_hevy()
            if client:
                try:
                    from hevy.client import _routine_id
                    from ai.coach import _show_exercise_benefits
                    resp = push_routine_to_hevy(routine)
                    console.print(f"\n[green]✓ Routine pushed to Hevy![/green] (id: {_routine_id(resp)})")
                    _show_exercise_benefits(routine.get("exercises", []))
                except Exception as e:
                    console.print(f"[red]{e}[/red]")


def _do_chat():
    if not _require_ai():
        return
    weeks_str = questionary.select(
        "Training context:",
        choices=[
            questionary.Choice("4 weeks",  value=4),
            questionary.Choice("8 weeks",  value=8),
            questionary.Choice("12 weeks", value=12),
            questionary.Choice("All time (16 weeks)", value=16),
        ],
        default=8,
        style=STYLE,
    ).ask()
    if not weeks_str:
        return
    weeks = int(weeks_str)
    from ai.coach import start_enhanced_chat
    start_enhanced_chat(weeks=weeks)


# ── settings & reset ─────────────────────────────────────────────────────────

_AI_LANGUAGES = [
    "English", "Portuguese", "Spanish", "French", "German",
    "Italian", "Dutch", "Polish", "Russian", "Japanese", "Chinese",
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

        from config import AI_MODEL
        lines = [
            f"Provider:      [bold]{AI_PROVIDER}[/bold]  ·  Model: [bold]{AI_MODEL}[/bold]",
            f"Context mode:  [bold]{'Slim  (fewer tokens)' if slim_on else 'Full  (all analytics)'}[/bold]",
            f"Language:      [bold]{lang}[/bold]",
            "",
            "Token usage (cumulative):",
            f"  Input:   [cyan]{usage['input']:,}[/cyan] tokens",
            f"  Output:  [cyan]{usage['output']:,}[/cyan] tokens",
            f"  Total:   [bold cyan]{total:,}[/bold cyan] tokens",
        ]
        if usage["cache_read"]:
            lines.append(f"  Cached:  [green]{usage['cache_read']:,}[/green] tokens  ({cache_pct}% of input)")

        console.print(Panel("\n".join(lines), title="[bold]AI Coach Settings[/bold]", border_style="cyan"))

        action = questionary.select(
            "AI settings:",
            choices=[
                questionary.Choice(
                    f"  Toggle context mode  (currently: {'Slim' if slim_on else 'Full'})",
                    value="toggle_slim",
                ),
                questionary.Choice(f"  Response language  (currently: {lang})", value="language"),
                questionary.Choice("  Reset token counter",  value="reset_tokens"),
                questionary.Separator("  ───"),
                questionary.Choice("  Back",                 value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "toggle_slim":
            new_val = "0" if slim_on else "1"
            set_pref("ai_chat_slim", new_val)
            label = "Slim (fewer tokens)" if new_val == "1" else "Full (all analytics)"
            console.print(f"[green]✓ Context mode set to: {label}[/green]")

        elif action == "language":
            choices = _AI_LANGUAGES + ([] if lang in _AI_LANGUAGES else [lang])
            new_lang = questionary.select(
                "  AI response language:",
                choices=choices,
                default=lang if lang in choices else choices[0],
                style=STYLE,
            ).ask()
            if new_lang:
                set_pref("ai_language", new_lang)
                console.print(f"[green]✓ Language set to {new_lang}[/green]")

        elif action == "reset_tokens":
            if questionary.confirm("  Reset all token counters to zero?", default=False, style=STYLE).ask():
                reset_token_usage()
                console.print("[green]✓ Token counters reset.[/green]")


def _do_data_reset():
    while True:
        console.clear()
        action = questionary.select(
            "What do you want to reset?",
            choices=[
                questionary.Choice("Clear coach memories  (forget past conversations)", value="memories"),
                questionary.Choice("Clear all goals",                                   value="goals"),
                questionary.Choice("Clear sync state  (next sync will re-download all)", value="sync_state"),
                questionary.Choice("Wipe everything  (delete all local data)",           value="all"),
                questionary.Separator("  ───"),
                questionary.Choice("Back",                                               value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "memories":
            if questionary.confirm(
                "  Delete all memories from past conversations?", default=False, style=STYLE
            ).ask():
                from db.memories import clear_memories
                clear_memories()
                console.print("[green]✓ Memories cleared.[/green]")

        elif action == "goals":
            if questionary.confirm("  Delete all goals?", default=False, style=STYLE).ask():
                from db.goals import clear_goals
                clear_goals()
                console.print("[green]✓ Goals cleared.[/green]")

        elif action == "sync_state":
            if questionary.confirm(
                "  Reset sync state? The next sync will re-download all workouts.", default=False, style=STYLE
            ).ask():
                from db.store import set_sync_state
                set_sync_state("last_sync", "1970-01-01T00:00:00Z")
                console.print("[green]✓ Sync state reset. Run Sync → Incremental to re-download.[/green]")

        elif action == "all":
            console.print(
                "\n  [bold red]This will delete hevy.db and disconnect Google Fit.[/bold red]\n"
                "  All workouts, goals, memories, and health data will be removed from this device.\n"
                "  Your data on Hevy and Google Fit is NOT affected.\n"
            )
            if not questionary.confirm("  Are you sure?", default=False, style=STYLE).ask():
                continue
            if not questionary.confirm("  Really? This cannot be undone.", default=False, style=STYLE).ask():
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

            console.print(
                "\n[bold green]✓ Everything wiped.[/bold green]\n"
                "  Run [bold]Sync → Full[/bold] to re-download your workouts."
            )
            return  # DB is gone — exit all the way back to main


def _do_create_profile_flow() -> str:
    """Interactive profile creation. Returns the new slug."""
    from profile_mgr import create_profile
    name = (questionary.text("  Profile name:", style=STYLE).ask() or "").strip()
    if not name:
        name = "New Profile"
    api_key = (questionary.text(
        "  Hevy API key (leave blank to set later):",
        style=STYLE,
    ).ask() or "").strip()
    profile = create_profile(name, hevy_api_key=api_key)
    console.print(f"[green]✓ Profile '{_esc(name)}' created.[/green]")
    return profile["slug"]


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
            f"Active profile: [bold]{_esc(active_name)}[/bold]  ({len(profiles)} total)",
            title="[bold]Profiles[/bold]",
            border_style="cyan",
            padding=(0, 2),
        ))

        action = questionary.select(
            "Profiles:",
            choices=[
                questionary.Choice("  Switch profile",         value="switch"),
                questionary.Choice("  Create new profile",     value="create"),
                questionary.Choice("  Rename current profile", value="rename"),
                questionary.Choice("  Delete a profile",       value="delete"),
                questionary.Separator("  ───────────────────────────────────────"),
                questionary.Choice("  Back",                   value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "switch":
            if len(profiles) <= 1:
                console.print("[dim]Only one profile exists. Create another to switch.[/dim]")
                questionary.press_any_key_to_continue(style=STYLE).ask()
                continue
            choices = [
                questionary.Choice(
                    f"  {p['name']}{' (active)' if p['slug'] == active_slug else ''}",
                    value=p["slug"],
                )
                for p in profiles
            ]
            choices.append(questionary.Separator("  ──────────────────────────────────"))
            choices.append(questionary.Choice("  Cancel", value=None))
            slug = questionary.select("Switch to:", choices=choices, style=STYLE).ask()
            if slug and slug != active_slug:
                set_active_slug(slug)
                console.print(f"[green]Switching to '{_esc(get_profile_name(slug))}'...[/green]")
                import os as _os
                import sys as _sys
                _os.execv(_sys.executable, [_sys.executable] + _sys.argv)

        elif action == "create":
            slug = _do_create_profile_flow()
            if questionary.confirm("  Switch to new profile now?", default=True, style=STYLE).ask():
                set_active_slug(slug)
                import os as _os
                import sys as _sys
                _os.execv(_sys.executable, [_sys.executable] + _sys.argv)

        elif action == "rename":
            if active_slug:
                new_name = (questionary.text(
                    f"  New name for '{_esc(active_name)}':",
                    style=STYLE,
                ).ask() or "").strip()
                if new_name:
                    rename_profile(active_slug, new_name)
                    console.print(f"[green]✓ Profile renamed to '{_esc(new_name)}'[/green]")

        elif action == "delete":
            others = [p for p in profiles if p["slug"] != active_slug]
            if not others:
                console.print("[dim]Cannot delete the only profile.[/dim]")
                questionary.press_any_key_to_continue(style=STYLE).ask()
                continue
            choices = [questionary.Choice(f"  {p['name']}", value=p["slug"]) for p in others]
            choices.append(questionary.Separator("  ──────────────────────────────────"))
            choices.append(questionary.Choice("  Cancel", value=None))
            slug = questionary.select("Delete which profile?", choices=choices, style=STYLE).ask()
            if slug:
                pname = get_profile_name(slug)
                if questionary.confirm(
                    f"  Delete '{_esc(pname)}'? This cannot be undone.",
                    default=False,
                    style=STYLE,
                ).ask():
                    delete_profile(slug)
                    console.print(f"[green]✓ Profile '{_esc(pname)}' deleted.[/green]")


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

    console.print(Panel(
        f"Display name:  [bold]{_esc(name) if name else '[dim]not set[/dim]'}[/bold]\n"
        f"Hevy API key:  [bold]{masked_key}[/bold]",
        title="[bold]Profile[/bold]",
        border_style="cyan",
        padding=(0, 2),
    ))

    action = questionary.select(
        "Edit:",
        choices=[
            questionary.Choice("  Display name",  value="name"),
            questionary.Choice("  Hevy API key",  value="apikey"),
            questionary.Choice("  Cancel",        value="back"),
        ],
        style=STYLE,
    ).ask()

    if action == "name":
        new_name = (questionary.text("  New display name:", style=STYLE).ask() or "").strip()
        if new_name:
            set_pref("display_name", new_name)
            console.print(f"[green]✓ Name updated to '{_esc(new_name)}'[/green]")

    elif action == "apikey" and active_slug:
        new_key = (questionary.text("  New Hevy API key:", style=STYLE).ask() or "").strip()
        if new_key:
            update_profile_key(active_slug, new_key)
            config.HEVY_API_KEY = new_key
            console.print("[green]✓ Hevy API key updated.[/green]")


def _do_preferences_settings() -> None:
    while True:
        console.clear()
        units = _get_units()
        checkin_days = int(get_pref("goals_checkin_days") or 7)
        auto_sync = get_pref("auto_sync") == "1"
        default_weeks = get_pref("default_stats_weeks") or "8 weeks"

        lines = [
            f"Weight units:          [bold]{units}[/bold]",
            f"Goal check-in:         [bold]every {checkin_days} days[/bold]",
            f"Auto-sync on startup:  [bold]{'on' if auto_sync else 'off'}[/bold]",
            f"Default stats window:  [bold]{default_weeks}[/bold]",
        ]
        console.print(Panel("\n".join(lines), title="[bold]Preferences[/bold]", border_style="cyan", padding=(0, 2)))

        action = questionary.select(
            "Change:",
            choices=[
                questionary.Choice(f"  Weight units              (currently: {units})", value="units"),
                questionary.Choice(f"  Goal check-in frequency   (every {checkin_days}d)", value="checkin"),
                questionary.Choice(f"  Auto-sync on startup      ({'on' if auto_sync else 'off'})", value="autosync"),
                questionary.Choice(f"  Default stats window      ({default_weeks})", value="stats_window"),
                questionary.Separator("  ───"),
                questionary.Choice("  Back", value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "units":
            new_units = questionary.select(
                "  Weight units:",
                choices=[
                    questionary.Choice("kg  (kilograms)", value="kg"),
                    questionary.Choice("lbs  (pounds)",   value="lbs"),
                ],
                default=units,
                style=STYLE,
            ).ask()
            if new_units:
                set_pref("units", new_units)
                console.print(f"[green]✓ Units set to {new_units}[/green]")

        elif action == "checkin":
            new_days = questionary.select(
                "  Goal check-in frequency:",
                choices=[
                    questionary.Choice("Every 7 days   (weekly)",    value="7"),
                    questionary.Choice("Every 14 days  (bi-weekly)", value="14"),
                    questionary.Choice("Every 30 days  (monthly)",   value="30"),
                ],
                default=str(checkin_days),
                style=STYLE,
            ).ask()
            if new_days:
                set_pref("goals_checkin_days", new_days)
                console.print(f"[green]✓ Goal check-in set to every {new_days} days[/green]")

        elif action == "autosync":
            set_pref("auto_sync", "0" if auto_sync else "1")
            console.print(f"[green]✓ Auto-sync {'disabled' if auto_sync else 'enabled'}[/green]")

        elif action == "stats_window":
            new_window = questionary.select(
                "  Default stats window:",
                choices=["4 weeks", "8 weeks", "12 weeks", "24 weeks"],
                default=default_weeks,
                style=STYLE,
            ).ask()
            if new_window:
                set_pref("default_stats_weeks", new_window)
                console.print(f"[green]✓ Default stats window set to {new_window}[/green]")


def _do_settings() -> None:
    while True:
        console.clear()
        action = questionary.select(
            "Settings:",
            choices=[
                questionary.Choice("  Profiles     (switch, create, rename, delete)", value="profiles"),
                questionary.Choice("  Profile      (display name)",                   value="profile"),
                questionary.Choice("  Preferences  (units, sync, check-in)",          value="prefs"),
                questionary.Choice("  AI Coach     (context mode, language)",          value="ai"),
                questionary.Separator("  ───────────────────────────────────────"),
                questionary.Choice("  Reset data",                                    value="reset"),
                questionary.Separator("  ───────────────────────────────────────"),
                questionary.Choice("  Back",                                          value="back"),
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
        "Google Fit:",
        choices=[
            questionary.Choice("Sync health data  (sleep, steps, calories, HR)", value="sync"),
            questionary.Choice("Connect / re-authenticate", value="connect"),
            questionary.Choice("View recovery dashboard", value="view"),
            questionary.Choice("Disconnect Google Fit", value="disconnect"),
        ],
        style=STYLE,
    ).ask()
    if not action:
        return

    if action == "connect":
        _fit_setup()

    elif action == "sync":
        if not is_connected():
            console.print("[yellow]Not connected. Choose 'Connect' first.[/yellow]")
            return
        days_str = questionary.select(
            "How far back to sync?",
            choices=["7 days", "14 days", "30 days", "90 days"],
            default="30 days",
            style=STYLE,
        ).ask()
        if not days_str:
            return
        days = int(days_str.split()[0])
        console.print(f"\n[dim]Syncing {days} days of Fit data...[/dim]")
        try:
            from fit.sync import sync_fit
            counts = sync_fit(days=days)
            console.print(Panel(
                f"[bold green]{counts['daily_days']}[/bold green] daily records  ·  "
                f"[bold green]{counts['sleep_sessions']}[/bold green] sleep sessions",
                title="[bold green]Google Fit sync complete[/bold green]",
                border_style="green",
            ))
            _render_recovery_panel()
        except Exception as e:
            console.print(f"[red]{e}[/red]")

    elif action == "view":
        if not is_connected():
            console.print("[yellow]Not connected.[/yellow]")
            return
        _render_fit_dashboard()

    elif action == "disconnect":
        if questionary.confirm("  Disconnect Google Fit? (local data stays)", default=False, style=STYLE).ask():
            disconnect()
            console.print("[dim]Disconnected. Local Fit data kept in DB.[/dim]")


def _fit_setup() -> None:
    console.rule("[bold cyan]Connect Google Fit[/bold cyan]")
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

    if not questionary.confirm("  Ready to authenticate?", default=True, style=STYLE).ask():
        return

    try:
        from fit.auth import get_credentials, CREDENTIALS_FILE
        get_credentials()
        console.print("\n[bold green]✓ Connected to Google Fit![/bold green]")
        console.print("[dim]Run 'Google Fit → Sync health data' to import your data.[/dim]\n")
    except FileNotFoundError as e:
        console.print(f"\n[red]{e}[/red]")  # safe: our own message, no secrets
    except Exception as e:
        console.print("\n[red]Authentication failed. Check that fit_credentials.json is valid.[/red]")
        console.print(f"[dim]{type(e).__name__}[/dim]")


def _render_fit_dashboard() -> None:
    from fit.analytics import sleep_summary, activity_summary, recovery_score

    console.rule("[bold green]Recovery Dashboard[/bold green]")

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
        t.add_column("Metric", style="bold")
        t.add_column("Value", justify="right")
        if sleep.get("avg_hours"):
            t.add_row("Avg sleep", f"{sleep['avg_hours']}h/night")
            t.add_row("Last night", f"{sleep.get('last_night_hours')}h")
            t.add_row("Nights ≥7h", f"{sleep['nights_7plus_hours']}/{sleep['nights_tracked']}")
        if activity.get("avg_steps"):
            t.add_row("Avg steps / day", f"{int(activity['avg_steps']):,}")
        if activity.get("avg_calories"):
            t.add_row("Avg calories / day", f"{int(activity['avg_calories']):,} kcal")
        if activity.get("resting_hr"):
            t.add_row("Resting HR", f"{activity['resting_hr']} bpm")
        if activity.get("avg_active_minutes"):
            t.add_row("Avg active minutes", str(int(activity["avg_active_minutes"])))
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
        return

    console.print()

    if stale_hevy:
        if auto_sync:
            try:
                console.print("[dim]Auto-syncing Hevy...[/dim]")
                client = _require_hevy()
                if client:
                    counts = incremental_sync(client)
                    console.print(f"[dim]Auto-synced Hevy: {counts['updated']} updated · {counts['deleted']} deleted.[/dim]")
            except Exception as e:
                console.print(f"[dim]Hevy auto-sync failed: {e}[/dim]")
        elif questionary.confirm(
            "  Hevy hasn't been synced in over 24h. Sync now?", default=True, style=STYLE
        ).ask():
            client = _require_hevy()
            if client:
                counts = incremental_sync(client)
                console.print(
                    f"[green]Hevy sync done:[/green] "
                    f"{counts['updated']} updated · {counts['deleted']} deleted."
                )

    if stale_fit:
        if auto_sync:
            try:
                console.print("[dim]Auto-syncing Google Fit...[/dim]")
                from fit.sync import sync_fit
                counts = sync_fit(days=30)
                console.print(f"[dim]Auto-synced Fit: {counts['daily_days']} days · {counts['sleep_sessions']} sleep sessions.[/dim]")
            except Exception as e:
                console.print(f"[dim]Fit auto-sync failed: {e}[/dim]")
        elif questionary.confirm(
            "  Google Fit hasn't been synced in over 24h. Sync now?", default=True, style=STYLE
        ).ask():
            try:
                from fit.sync import sync_fit
                counts = sync_fit(days=90)
                console.print(
                    f"[green]Google Fit sync done:[/green] "
                    f"{counts['daily_days']} days · {counts['sleep_sessions']} sleep sessions."
                )
            except Exception as e:
                console.print(f"[red]Fit sync failed: {e}[/red]")


def _check_goals_and_checkin() -> None:
    if should_ask_goals():
        goals = get_goals()
        if not goals:
            # First time ever
            if questionary.confirm(
                "  No goals set yet. Set your training goals now?", default=True, style=STYLE
            ).ask():
                run_goals_wizard()
        else:
            # Weekly check-in
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
        fit_label = "  Google Fit  ✓  (sleep, steps, HR)" if _fit_connected() else "  Google Fit  (not connected)"
    except Exception:
        fit_label = "  Google Fit  (sleep, steps, HR)"

    last_action = get_pref("last_menu_action")
    items = [
        questionary.Choice("  Sync new workouts",    value="sync"),
        questionary.Choice("  Chat with coach",      value="chat"),
        questionary.Separator("  ──────────────────────────────────"),
        questionary.Choice("  My goals",              value="goals"),
        questionary.Choice("  Dashboard & stats",     value="stats"),
        questionary.Choice("  Exercise progression",  value="progress"),
        questionary.Choice("  Personal records",      value="records"),
        questionary.Separator("  ──────────────────────────────────"),
        questionary.Choice("  AI coaching report",    value="coach"),
        questionary.Choice(fit_label,                 value="fit"),
        questionary.Separator("  ──────────────────────────────────"),
        questionary.Choice("  Settings",              value="settings"),
        questionary.Choice("  Exit",                  value="exit"),
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
            "Lifter now supports multiple profiles.\n"
            "Your existing data will be migrated to a named profile.",
            title="[bold cyan]Profile Migration[/bold cyan]",
            border_style="cyan",
            padding=(0, 2),
        ))
        name = (questionary.text(
            "  Profile name (e.g. your name):",
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
        console.print(f"[green]✓ Profile '{_esc(name)}' created and data migrated.[/green]")
        console.print()
        return

    profiles = list_profiles()

    if not profiles:
        # First run — prompt for name and Hevy API key
        console.print()
        console.rule("[bold cyan]Welcome to Lifter[/bold cyan]")
        name = (questionary.text(
            "  Your name (for the AI coach):",
            default="Athlete",
            style=STYLE,
        ).ask() or "Athlete").strip()
        api_key = (questionary.text(
            "  Hevy API key (hevy.com → Settings → Developer):",
            style=STYLE,
        ).ask() or "").strip()
        profile = create_profile(name, hevy_api_key=api_key)
        activate_profile(profile["slug"])
        console.print()
        return

    if len(profiles) == 1:
        activate_profile(profiles[0]["slug"])
        return

    # Multiple profiles — show selector
    last = get_active_slug()
    choices = []
    for p in profiles:
        suffix = " (last used)" if p["slug"] == last else ""
        choices.append(questionary.Choice(f"  {p['name']}{suffix}", value=p["slug"]))
    choices.append(questionary.Separator("  ──────────────────────────────────"))
    choices.append(questionary.Choice("  + Create new profile", value="_new"))

    console.clear()
    slug = questionary.select("Select profile:", choices=choices, style=STYLE).ask()

    if not slug:
        slug = last or profiles[0]["slug"]
    elif slug == "_new":
        slug = _do_create_profile_flow()

    set_active_slug(slug)
    activate_profile(slug)


def main():
    _bootstrap_profiles()
    init_db()
    _check_goals_and_checkin()
    _check_stale_sync()

    while True:
        console.clear()
        _show_header()
        _render_snapshot_panel()
        menu_items, menu_default = _build_menu()

        choice = questionary.select(
            "What do you want to do?",
            choices=menu_items,
            default=menu_default,
            style=STYLE,
        ).ask()

        if choice is None or choice == "exit":
            console.print("\n[dim]See you at the gym![/dim]\n")
            break

        set_pref("last_menu_action", choice)
        console.clear()
        action = ACTIONS.get(choice)
        if action:
            action()

        if choice not in _NO_PAUSE:
            _pause()


if __name__ == "__main__":
    main()
