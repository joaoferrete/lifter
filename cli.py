"""hevy — personal Hevy workout client with AI coaching."""
import json
from datetime import datetime, timezone
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich import box

from config import HEVY_API_KEY, GEMINI_API_KEY
from hevy.client import HevyClient
from hevy.sync import full_sync, incremental_sync
from db.store import init_db, query
from analytics.volume import muscle_group_summary, sets_per_muscle_per_week, weekly_volume
from analytics.progression import detect_plateaus, top_progressions, exercise_progression
from analytics.frequency import workout_frequency, muscle_group_frequency
from analytics.records import all_time_records, recent_prs, body_measurement_trend

app = typer.Typer(help="Personal Hevy workout client with AI coaching.")
console = Console()


def _require_api_key() -> HevyClient:
    if not HEVY_API_KEY:
        console.print("[red]HEVY_API_KEY not set. Copy .env.example → .env and fill it in.[/red]")
        raise typer.Exit(1)
    return HevyClient()


# ── sync report helpers ────────────────────────────────────────────────────────

def _fmt_duration(start_iso: str, end_iso: str) -> str:
    try:
        s = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        mins = int((e - s).total_seconds() / 60)
        return f"{mins} min"
    except Exception:
        return ""


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
            SELECT we.title, we.exercise_template_id, we.id as we_id,
                   ws.weight_kg, ws.reps,
                   ws.weight_kg * (1 + ws.reps / 30.0) as e1rm
            FROM workout_exercises we
            JOIN workout_sets ws ON ws.workout_exercise_id = we.id
            WHERE we.workout_id = ?
              AND ws.type = 'normal'
              AND ws.weight_kg IS NOT NULL
              AND ws.reps IS NOT NULL
              AND ws.reps > 0
            ORDER BY we.idx, e1rm DESC
            """,
            (wid,),
        )

        # Keep best set per exercise
        best: dict[str, dict] = {}
        for ex in exercises:
            name = ex["title"]
            if name not in best or ex["e1rm"] > best[name]["e1rm"]:
                best[name] = ex

        lines = []
        for name, ex in best.items():
            prev = query(
                """
                SELECT MAX(ws.weight_kg * (1 + ws.reps / 30.0)) as top
                FROM workout_sets ws
                WHERE ws.exercise_template_id = ?
                  AND ws.type = 'normal'
                  AND ws.weight_kg IS NOT NULL
                  AND ws.reps IS NOT NULL
                  AND ws.workout_id != ?
                """,
                (ex["exercise_template_id"], wid),
            )
            prev_best = prev[0]["top"] if prev and prev[0]["top"] else 0
            is_pr = ex["e1rm"] > prev_best

            weight_str = f"{ex['weight_kg']} kg × {ex['reps']} reps"
            if is_pr:
                lines.append(f"  [bold]{name}[/bold]  {weight_str}  [bold yellow]★ PR[/bold yellow]  [dim](e1RM {ex['e1rm']:.1f} kg)[/dim]")
            else:
                lines.append(f"  [bold]{name}[/bold]  {weight_str}")

        if not lines:
            # Duration-only or bodyweight exercises — just list names
            bw_exs = query(
                "SELECT DISTINCT we.title FROM workout_exercises we WHERE we.workout_id = ?",
                (wid,),
            )
            lines = [f"  {e['title']}" for e in bw_exs]

        body = "\n".join(lines) if lines else "  (no logged sets)"
        console.print(
            Panel(
                body,
                title=f"[bold cyan]{w['title']}[/bold cyan]  [dim]{date_str} · {duration}[/dim]",
                border_style="cyan",
                padding=(0, 1),
            )
        )


def _render_streak() -> None:
    freq = workout_frequency(4)
    streak = freq.get("longest_streak_days", 0)
    total = freq.get("total_workouts", 0)
    if streak >= 2:
        fires = "🔥" * min(streak, 5)
        console.print(f"\n  {fires}  [bold]{streak}-day training streak![/bold]  [dim]({total} sessions in last 4 weeks)[/dim]")
    elif total > 0:
        console.print(f"\n  [dim]{total} sessions in the last 4 weeks.[/dim]")


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

        bar_width = max(1, int(curr / max_vol * 18))
        bar = "█" * bar_width + "░" * (18 - bar_width)

        if prev > 0:
            pct = (curr - prev) / prev * 100
            color = "green" if pct >= 0 else "red"
            sign = "+" if pct >= 0 else ""
            delta = f" [{color}]{sign}{pct:.0f}%[/{color}]"
        else:
            delta = " [dim]new[/dim]"

        console.print(f"    {muscle:<14} [cyan]{bar}[/cyan] {curr:>6.0f} kg{delta}")


def _render_sync_report(counts: dict, is_full: bool) -> None:
    updated_ids: list[str] = counts.get("updated_ids", [])

    if is_full:
        console.print(
            Panel(
                f"[bold green]{counts.get('workouts', 0)}[/bold green] workouts  ·  "
                f"[bold]{counts.get('templates', 0)}[/bold] exercise templates  ·  "
                f"[bold]{counts.get('body_measurements', 0)}[/bold] body measurements",
                title="[bold green]Full sync complete[/bold green]",
                border_style="green",
            )
        )
        _render_streak()
        _render_volume_delta()
        return

    updated = counts.get("updated", 0)
    deleted = counts.get("deleted", 0)

    if updated == 0 and deleted == 0:
        console.print("[dim]Already up to date.[/dim]")
        return

    parts = []
    if updated:
        parts.append(f"[bold green]{updated}[/bold green] new/updated")
    if deleted:
        parts.append(f"[bold red]{deleted}[/bold red] deleted")

    console.print(Panel(" · ".join(parts), title="[bold green]Sync complete[/bold green]", border_style="green"))

    if updated_ids:
        show_ids = updated_ids[:4]
        _render_workout_cards(show_ids)
        if len(updated_ids) > 4:
            console.print(f"  [dim]...and {len(updated_ids) - 4} more workout(s)[/dim]")

    _render_streak()
    _render_volume_delta()
    console.print()


# ── sync ──────────────────────────────────────────────────────────────────────

@app.command()
def sync(
    full: bool = typer.Option(False, "--full", "-f", help="Force a full re-sync instead of incremental."),
):
    """Sync workout data from Hevy and show a summary of what changed."""
    init_db()
    client = _require_api_key()

    if full:
        console.print("[bold]Running full sync...[/bold]")
        counts = full_sync(client)
    else:
        counts = incremental_sync(client)

    _render_sync_report(counts, is_full=full)


# ── stats ─────────────────────────────────────────────────────────────────────

@app.command()
def stats(
    weeks: int = typer.Option(8, "--weeks", "-w", help="How many weeks to analyse."),
):
    """Show training analytics summary."""
    freq = workout_frequency(weeks)
    if freq["total_workouts"] == 0:
        console.print("[yellow]No workout data found. Run `hevy sync` first.[/yellow]")
        raise typer.Exit(0)

    console.rule(f"[bold cyan]Training Stats — last {weeks} weeks[/bold cyan]")

    t = Table(box=box.SIMPLE)
    t.add_column("Metric", style="bold")
    t.add_column("Value", justify="right")
    t.add_row("Total workouts", str(freq["total_workouts"]))
    t.add_row("Avg workouts / week", str(freq["avg_per_week"]))
    t.add_row("Avg session duration", f"{freq['avg_duration_minutes']} min")
    t.add_row("Avg rest between sessions", f"{freq['rest_day_avg']} days")
    t.add_row("Longest training streak", f"{freq['longest_streak_days']} days")
    console.print(t)

    console.rule("[bold]Volume by muscle group[/bold]")
    muscle_vol = muscle_group_summary(weeks)
    sets_wk = sets_per_muscle_per_week(weeks)
    muscle_freq_data = muscle_group_frequency(weeks)
    max_vol = max(muscle_vol.values()) if muscle_vol else 1.0

    t2 = Table(box=box.SIMPLE)
    t2.add_column("Muscle group", style="bold")
    t2.add_column("Weekly volume", no_wrap=True)
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
        console.rule("[bold yellow]Plateau warnings[/bold yellow]")
        for p in plateaus:
            console.print(f"  [yellow]•[/yellow] {p['exercise']} — stalled {p['sessions_stalled']} sessions (e1RM {p['current_e1rm']} kg)")


# ── progress ──────────────────────────────────────────────────────────────────

@app.command()
def progress(
    exercise: Optional[str] = typer.Argument(None, help="Exercise name to inspect (partial match). Omit for top gainers."),
    weeks: int = typer.Option(12, "--weeks", "-w"),
):
    """Show progression for a specific exercise or list the biggest gainers."""
    if exercise:
        rows = query(
            "SELECT id, title FROM exercise_templates WHERE lower(title) LIKE lower(?)",
            (f"%{exercise}%",),
        )
        if not rows:
            console.print(f"[red]No exercise found matching '{exercise}'[/red]")
            raise typer.Exit(1)

        template = rows[0]
        console.rule(f"[bold]{template['title']}[/bold]")

        df = exercise_progression(template["id"], weeks)
        if df.empty:
            console.print("[yellow]No progression data.[/yellow]")
            return

        t = Table(box=box.SIMPLE)
        t.add_column("Date")
        t.add_column("Best weight", justify="right")
        t.add_column("Reps", justify="right")
        t.add_column("e1RM", justify="right")
        for _, row in df.iterrows():
            t.add_row(str(row["date"]), f"{row['best_weight_kg']} kg", str(row["best_reps"]), f"{row['e1rm']:.1f} kg")
        console.print(t)
    else:
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


# ── records ───────────────────────────────────────────────────────────────────

@app.command()
def records():
    """Show all-time personal records."""
    prs = all_time_records()
    if not prs:
        console.print("[yellow]No records yet. Run `hevy sync` first.[/yellow]")
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


# ── coach ─────────────────────────────────────────────────────────────────────

@app.command()
def coach(
    weeks: int = typer.Option(8, "--weeks", "-w", help="Weeks of history to analyse."),
    push: bool = typer.Option(False, "--push", help="Push the generated routine to Hevy after review."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Save full JSON response to file."),
):
    """Get AI-powered coaching insights and a suggested next routine."""
    if not GEMINI_API_KEY:
        console.print("[red]GEMINI_API_KEY not set in .env[/red]")
        raise typer.Exit(1)

    from ai.coach import get_coaching, push_routine_to_hevy

    console.rule("[bold cyan]AI Coaching Report[/bold cyan]")

    try:
        result = get_coaching(weeks=weeks, stream=True)
    except Exception as e:
        console.print(f"[red]Gemini error: {e}[/red]")
        raise typer.Exit(1)

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

    if output:
        with open(output, "w") as f:
            json.dump(result, f, indent=2)
        console.print(f"\n[dim]Full response saved to {output}[/dim]")

    if push and routine:
        console.print("\n[bold]Pushing routine to Hevy...[/bold]")
        try:
            _require_api_key()
            resp = push_routine_to_hevy(routine)
            console.print(f"[green]Routine created: {resp.get('routine', {}).get('id')}[/green]")
        except Exception as e:
            console.print(f"[red]Failed to push routine: {e}[/red]")
    elif routine and not push:
        console.print("\n[dim]Tip: run with --push to send this routine directly to your Hevy app.[/dim]")


# ── chat ──────────────────────────────────────────────────────────────────────

@app.command()
def chat(
    weeks: int = typer.Option(8, "--weeks", "-w", help="Weeks of history to load as context."),
):
    """Interactive AI chat about your training. Ask anything."""
    if not GEMINI_API_KEY:
        console.print("[red]GEMINI_API_KEY not set in .env[/red]")
        raise typer.Exit(1)

    from ai.coach import start_chat

    freq = workout_frequency(4)
    if freq["total_workouts"] == 0:
        console.print("[yellow]No workout data. Run `hevy sync` first.[/yellow]")
        raise typer.Exit(0)

    console.rule("[bold cyan]AI Training Chat[/bold cyan]")
    console.print(
        f"  [dim]Context: last {weeks} weeks · {freq['total_workouts']} sessions · "
        f"{freq['avg_per_week']}/wk avg[/dim]"
    )
    console.print("  [dim]Type your question. Ctrl+C or 'quit' to exit.[/dim]\n")

    try:
        start_chat(weeks=weeks)
    except KeyboardInterrupt:
        console.print("\n[dim]See you at the gym![/dim]")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
