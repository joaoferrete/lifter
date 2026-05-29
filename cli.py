"""hevy — interactive personal Hevy workout client."""
import json
import sys
from datetime import datetime, timezone
from typing import Optional

import questionary
from rich.columns import Columns
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich import box

from config import HEVY_API_KEY, GEMINI_API_KEY
from db.store import init_db, query, get_sync_state
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


def _require_hevy() -> HevyClient:
    if not HEVY_API_KEY:
        console.print("[red]HEVY_API_KEY not set in .env[/red]")
        return None
    return HevyClient()


def _pause():
    console.print()
    questionary.press_any_key_to_continue("  Press any key to return to menu...").ask()


# ── header ────────────────────────────────────────────────────────────────────

def _show_header():
    last_sync = get_sync_state("last_sync")
    counts = query("SELECT COUNT(*) as n FROM workouts")
    total = counts[0]["n"] if counts else 0

    freq = workout_frequency(4)
    this_week = query(
        "SELECT COUNT(*) as n FROM workouts WHERE start_time >= datetime('now', '-7 days')"
    )
    week_count = this_week[0]["n"] if this_week else 0

    sync_str = f"Last sync: {_time_ago(last_sync)}" if last_sync else "Never synced — run Sync first"
    stats_str = f"{total} workouts total  ·  {week_count} this week  ·  {freq['avg_per_week']}/wk avg"

    console.print(
        Panel(
            f"[dim]{sync_str}[/dim]\n{stats_str}",
            title="[bold cyan]HEVY TRAINING CLIENT[/bold cyan]",
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()


# ── sync report helpers ────────────────────────────────────────────────────────

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
            """
            SELECT we.title, we.exercise_template_id,
                   ws.weight_kg, ws.reps,
                   ws.weight_kg * (1 + ws.reps / 30.0) as e1rm
            FROM workout_exercises we
            JOIN workout_sets ws ON ws.workout_exercise_id = we.id
            WHERE we.workout_id = ?
              AND ws.type = 'normal'
              AND ws.weight_kg IS NOT NULL AND ws.reps IS NOT NULL AND ws.reps > 0
            ORDER BY we.idx, e1rm DESC
            """,
            (wid,),
        )

        best: dict[str, dict] = {}
        for ex in exercises:
            name = ex["title"]
            if name not in best or ex["e1rm"] > best[name]["e1rm"]:
                best[name] = ex

        lines = []
        for name, ex in best.items():
            prev = query(
                """SELECT MAX(ws.weight_kg * (1 + ws.reps / 30.0)) as top
                   FROM workout_sets ws
                   WHERE ws.exercise_template_id = ? AND ws.type = 'normal'
                     AND ws.weight_kg IS NOT NULL AND ws.workout_id != ?""",
                (ex["exercise_template_id"], wid),
            )
            is_pr = ex["e1rm"] > (prev[0]["top"] or 0) if prev else False
            pr_badge = "  [bold yellow]★ PR[/bold yellow]" if is_pr else ""
            lines.append(
                f"  [bold]{name}[/bold]  "
                f"{ex['weight_kg']} kg × {ex['reps']} reps{pr_badge}"
            )

        if not lines:
            bw = query("SELECT DISTINCT we.title FROM workout_exercises we WHERE we.workout_id = ?", (wid,))
            lines = [f"  {e['title']}" for e in bw]

        console.print(
            Panel(
                "\n".join(lines) if lines else "  (no logged sets)",
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
    console.print("\n  [bold]Volume this week vs last week[/bold]")
    for muscle in curr_ser.index:
        curr = float(curr_ser[muscle])
        prev = float(prev_ser.get(muscle, 0))
        bar = "█" * max(1, int(curr / max_vol * 18)) + "░" * (18 - max(1, int(curr / max_vol * 18)))
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
            return
        parts = []
        if updated:
            parts.append(f"[bold green]{updated}[/bold green] new/updated")
        if deleted:
            parts.append(f"[bold red]{deleted}[/bold red] deleted")
        console.print(Panel(" · ".join(parts), title="[bold green]Sync complete[/bold green]", border_style="green"))

    if updated_ids:
        show = updated_ids[:4]
        _render_workout_cards(show)
        if len(updated_ids) > 4:
            console.print(f"  [dim]...and {len(updated_ids) - 4} more[/dim]")

    # Streak
    freq = workout_frequency(4)
    streak = freq.get("longest_streak_days", 0)
    if streak >= 2:
        fires = "🔥" * min(streak, 5)
        console.print(f"\n  {fires}  [bold]{streak}-day streak![/bold]  [dim]({freq['total_workouts']} sessions in last 4w)[/dim]")

    _render_volume_delta()
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
        t2.add_row(
            muscle,
            f"[cyan]{bar}[/cyan]",
            f"{vol:.0f} kg",
            str(sets_wk.get(muscle, 0)),
            str(muscle_freq_data.get(muscle, 0)),
        )
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
            "Search exercise:",
            choices=names,
            style=STYLE,
            validate=lambda v: v in names or "Pick from the list",
        ).ask()
        if not name:
            return

        rows = query("SELECT id FROM exercise_templates WHERE title = ?", (name,))
        if not rows:
            console.print(f"[red]Exercise not found.[/red]")
            return

        console.rule(f"[bold]{name}[/bold]")
        df = exercise_progression(rows[0]["id"], weeks)
        if df.empty:
            console.print("[yellow]No progression data for this exercise.[/yellow]")
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
            t.add_row(
                str(row["date"]),
                f"{row['best_weight_kg']} kg",
                str(row["best_reps"]),
                f"{row['e1rm']:.1f} kg{change}",
            )
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


def _do_coach():
    if not GEMINI_API_KEY:
        console.print("[red]GEMINI_API_KEY not set in .env[/red]")
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
        result = get_coaching(weeks=weeks, stream=True)
    except Exception as e:
        console.print(f"[red]Gemini error: {e}[/red]")
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
                f"[dim]{s['type']}[/dim] {s.get('weight_kg') or 'BW'}kg×{s.get('reps', '?')}"
                for s in ex.get("sets", [])
            )
            console.print(f"  [bold]{ex['title']}[/bold]")
            console.print(f"    {sets_str}")
            if ex.get("notes"):
                console.print(f"    [dim italic]{ex['notes']}[/dim italic]")

        console.print()
        push = questionary.confirm(
            f"  Push '{routine.get('title')}' to your Hevy app?",
            default=False,
            style=STYLE,
        ).ask()
        if push:
            client = _require_hevy()
            if client:
                try:
                    resp = push_routine_to_hevy(routine)
                    console.print(f"\n[green]✓ Routine pushed to Hevy![/green] (id: {resp.get('routine', {}).get('id')})")
                except Exception as e:
                    console.print(f"[red]Failed: {e}[/red]")


def _do_chat():
    if not GEMINI_API_KEY:
        console.print("[red]GEMINI_API_KEY not set in .env[/red]")
        return

    weeks_str = questionary.select(
        "Training context:",
        choices=["4 weeks", "8 weeks", "12 weeks", "All time (16 weeks)"],
        default="8 weeks",
        style=STYLE,
    ).ask()
    if not weeks_str:
        return
    weeks = int(weeks_str.split()[0])

    from ai.coach import start_enhanced_chat
    start_enhanced_chat(weeks=weeks)


# ── main loop ─────────────────────────────────────────────────────────────────

MENU_ITEMS = [
    questionary.Choice("  Sync new workouts",        value="sync"),
    questionary.Choice("  Dashboard & stats",         value="stats"),
    questionary.Choice("  Exercise progression",      value="progress"),
    questionary.Choice("  Personal records",          value="records"),
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
    "coach":    _do_coach,
    "chat":     _do_chat,
}

# Actions that manage their own "return to menu" flow
_NO_PAUSE = {"chat"}


def main():
    init_db()

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
