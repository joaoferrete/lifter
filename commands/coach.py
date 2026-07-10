"""Coach — AI coaching report and interactive chat menu actions."""

import questionary
from rich.panel import Panel

from analytics.volume import sets_per_muscle_per_week
from commands._shared import NO_PAUSE, _dlog
from db.goals import mark_report_generated, set_pref
from i18n import _
from ui.console import STYLE, console
from ui.console import score_color as _score_color
from ui.format import fmt_weight as _fmt_weight
from ui.prompts import week_choices as _week_choices
from ui.widgets import score_bar as _fmt_score_bar


def _do_coach() -> None:
    from cli import _report_weeks, _require_ai

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
    from cli import _require_hevy, _sets_by_group

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


def _do_chat() -> object:
    from cli import _require_ai

    if not _require_ai():
        return None  # None ⇒ main loop pauses, so the error stays visible
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
