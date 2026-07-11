"""Sync — manual Hevy sync and the post-sync report rendering."""

from datetime import datetime

import questionary
from rich.markup import escape as _esc
from rich.panel import Panel

from analytics.frequency import workout_frequency
from analytics.records import max_e1rm_excluding_workout, workout_best_sets
from analytics.volume import weekly_volume
from commands._shared import _dlog
from commands.goals import _render_goals_progress
from db.store import query, workout_exercise_titles
from hevy.sync import full_sync, incremental_sync
from i18n import _
from ui.console import STYLE, console
from ui.format import fmt_duration as _fmt_duration
from ui.format import fmt_weight as _fmt_weight

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
        exercises = workout_best_sets(wid)
        best: dict[str, dict] = {}
        for ex in exercises:
            n = ex["title"]
            if n not in best or ex["e1rm"] > best[n]["e1rm"]:
                best[n] = ex

        lines = []
        for name, ex in best.items():
            prev_top = max_e1rm_excluding_workout(ex["exercise_template_id"], wid)
            is_pr = ex["e1rm"] > (prev_top or 0)
            pr_badge = "  [bold yellow]★ PR[/bold yellow]" if is_pr else ""
            lines.append(f"  [bold]{name}[/bold]  {_fmt_weight(ex['weight_kg'])} × {ex['reps']} reps{pr_badge}")

        if not lines:
            lines = [f"  {title}" for title in workout_exercise_titles(wid)]

        console.print(
            Panel(
                "\n".join(lines) if lines else _("sync.no_sets_logged"),
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
                _(
                    "sync.full_body",
                    workouts=counts.get("workouts", 0),
                    templates=counts.get("templates", 0),
                    body=counts.get("body_measurements", 0),
                ),
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
        console.print("\n  " + _("sync.streak_line", fires=fires, streak=streak, sessions=freq["total_workouts"]))

    _render_volume_delta()
    console.print()
    _render_goals_progress()


def _do_sync() -> None:
    from cli import _require_hevy

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
