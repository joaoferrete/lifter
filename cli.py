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
from config import HEVY_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY, AI_PROVIDER
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
    if not HEVY_API_KEY:
        console.print("[red]HEVY_API_KEY not set in .env[/red]")
        return None
    return HevyClient()


def _require_ai() -> bool:
    if AI_PROVIDER == "claude" and not ANTHROPIC_API_KEY:
        console.print("[red]ANTHROPIC_API_KEY not set in .env (AI_PROVIDER=claude)[/red]")
        return False
    if AI_PROVIDER == "gemini" and not GEMINI_API_KEY:
        console.print("[red]GEMINI_API_KEY not set in .env (AI_PROVIDER=gemini)[/red]")
        return False
    return True


def _pause():
    console.print()
    questionary.press_any_key_to_continue("  Press any key to return to menu...").ask()


# ── goals wizard ──────────────────────────────────────────────────────────────

def _wizard_lift_prs() -> None:
    exercises = query("SELECT id, title FROM exercise_templates ORDER BY title")
    if not exercises:
        console.print("[yellow]  No exercises found. Run Sync first.[/yellow]")
        return
    names = [e["title"] for e in exercises]
    id_by_name = {e["title"]: e["id"] for e in exercises}

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
        current_e1rm = round(rows[0]["e1rm"], 1) if rows and rows[0]["e1rm"] else 0

        target_str = questionary.text(
            f"  Target weight in kg? (your current e1RM: {current_e1rm} kg)",
            style=STYLE,
            validate=lambda v: (v == "" or v.replace(".", "").isdigit()) or "Enter a number",
        ).ask()
        if not target_str:
            break
        target = float(target_str)
        save_goal(
            type="lift_pr",
            description=f"{name} — {int(target)} kg",
            target=target,
            unit="kg",
            exercise_template_id=template_id,
            exercise_name=name,
        )
        console.print(f"  [green]✓[/green] Goal saved: {name} {int(target)} kg\n")

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
    current = rows[0]["weight_kg"] if rows else None
    hint = f" (current: {current} kg)" if current else ""

    target_str = questionary.text(
        f"  Target body weight in kg{hint}:",
        style=STYLE,
        validate=lambda v: v.replace(".", "").isdigit() or "Enter a number",
    ).ask()
    if not target_str:
        return
    target = float(target_str)
    direction = "Lose" if goal_type == "weight_loss" else "Gain"
    save_goal(
        type=goal_type,
        description=f"{direction} weight to {target} kg",
        target=target,
        unit="kg",
        start_value=current,
    )
    console.print(f"  [green]✓[/green] Goal saved: {direction} to {target} kg\n")


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
            lines.append(f"  [bold]{name}[/bold]  {ex['weight_kg']} kg × {ex['reps']} reps{pr_badge}")

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
        console.print(f"    {muscle:<14} [cyan]{bar}[/cyan] {curr:>6.0f} kg{delta}")


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

    from ai.provider import provider_label
    sync_str = f"Last sync: {_time_ago(last_sync)}" if last_sync else "Never synced — run Sync first"
    title = f"LIFTER  [dim]·[/dim]  {_esc(name)}" if name else "LIFTER"
    ai_str = f"AI: {provider_label()}"

    # Recovery line from Google Fit (if connected + data available)
    recovery_str = ""
    try:
        from fit.auth import is_connected
        if is_connected():
            from fit.analytics import recovery_score
            rec = recovery_score(3)
            if rec:
                recovery_str = f"  ·  Recovery [{rec['color']}]{rec['score']}/100 {rec['label']}[/{rec['color']}]"
    except Exception:
        pass

    console.print(Panel(
        f"[dim]{sync_str}  ·  {ai_str}[/dim]\n"
        f"[bold]{total}[/bold] workouts total  ·  [bold]{week_count}[/bold] this week  ·  "
        f"[bold]{freq['avg_per_week']}[/bold]/wk avg"
        + (f"  ·  [yellow]{len(goals)} active goal(s)[/yellow]" if goals else "")
        + recovery_str,
        title=f"[bold cyan]{title}[/bold cyan]",
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
    weeks_str = questionary.select(
        "Time period:",
        choices=["4 weeks", "8 weeks", "12 weeks", "24 weeks"],
        default="8 weeks",
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
        t2.add_row(muscle, f"[cyan]{bar}[/cyan]", f"{vol:.0f} kg",
                   str(sets_wk.get(muscle, 0)), str(muscle_freq_data.get(muscle, 0)))
    console.print(t2)

    body = body_measurement_trend(weeks)
    if body:
        console.rule("[bold]Body measurements[/bold]")
        bt = Table(box=box.SIMPLE)
        bt.add_column("Metric", style="bold")
        bt.add_column("Latest", justify="right")
        bt.add_column(f"Change ({weeks}w)", justify="right")
        bt.add_row("Weight", f"{body.get('weight_kg')} kg", f"{body.get('weight_change_kg', '—')} kg")
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
            pt.add_row(pr["exercise"], f"{pr['weight_kg']} kg", str(pr["reps"]), f"{pr['e1rm']} kg", pr["date"])
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
            t.add_row(g["exercise"], f"+{g['improvement_pct']}%", f"{g['start_e1rm']} kg", f"{g['current_e1rm']} kg")
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
                    change = f" [green]+{delta:.1f}[/green]"
                elif delta < 0:
                    change = f" [red]{delta:.1f}[/red]"
            t.add_row(str(row["date"]), f"{row['best_weight_kg']} kg", str(row["best_reps"]), f"{row['e1rm']:.1f} kg{change}")
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
        t.add_row(pr["exercise"], f"{pr['weight_kg']} kg", str(pr["reps"]), f"{pr['e1rm']} kg", pr["date"])
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
        console.print("[red]AI request failed. Check your API key and network connection.[/red]")
        console.print(f"[dim]{type(e).__name__}[/dim]")
        return

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

    routine = result.get("routine", {})
    if routine:
        console.rule(f"[bold]Suggested routine: {routine.get('title')}[/bold]")
        console.print(f"  [dim]{routine.get('notes')}[/dim]\n")
        for ex in routine.get("exercises", []):
            sets_str = "  ".join(
                f"[dim]{s.get('type', 'normal')}[/dim] {s.get('weight_kg') or 'BW'}kg×{s.get('reps', '?')}"
                for s in ex.get("sets", [])
            )
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
                    resp = push_routine_to_hevy(routine)
                    console.print(f"\n[green]✓ Routine pushed to Hevy![/green] (id: {resp.get('routine', {}).get('id')})")
                except Exception as e:
                    console.print("[red]Failed to push routine. Check your Hevy API key.[/red]")
                    console.print(f"[dim]{type(e).__name__}[/dim]")


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
            console.print("[red]Google Fit sync failed. Check your credentials and internet connection.[/red]")
            console.print(f"[dim]{type(e).__name__}[/dim]")

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

MENU_ITEMS = [
    questionary.Choice("  Sync new workouts",        value="sync"),
    questionary.Choice("  Dashboard & stats",         value="stats"),
    questionary.Choice("  Exercise progression",      value="progress"),
    questionary.Choice("  Personal records",          value="records"),
    questionary.Choice("  My goals",                  value="goals"),
    questionary.Separator("  ─────────────────────────────────"),
    questionary.Choice("  Google Fit  (sleep, steps, HR)", value="fit"),
    questionary.Separator("  ─────────────────────────────────"),
    questionary.Choice("  AI coaching report",        value="coach"),
    questionary.Choice("  Chat with coach",           value="chat"),
    questionary.Separator("  ─────────────────────────────────"),
    questionary.Choice("  Exit",                      value="exit"),
]

ACTIONS = {
    "sync":     _do_sync,
    "stats":    _do_stats,
    "progress": _do_progress,
    "records":  _do_records,
    "goals":    _do_goals,
    "fit":      _do_fit,
    "coach":    _do_coach,
    "chat":     _do_chat,
}

_NO_PAUSE = {"chat"}


def main():
    init_db()
    _check_goals_and_checkin()

    while True:
        console.clear()
        _show_header()

        choice = questionary.select(
            "What do you want to do?",
            choices=MENU_ITEMS,
            style=STYLE,
        ).ask()

        if choice is None or choice == "exit":
            console.print("\n[dim]See you at the gym![/dim]\n")
            break

        console.clear()
        action = ACTIONS.get(choice)
        if action:
            action()

        if choice not in _NO_PAUSE:
            _pause()


if __name__ == "__main__":
    main()
