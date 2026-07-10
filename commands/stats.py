"""Stats — training statistics, progression views, and personal records."""

import questionary
from rich import box
from rich.table import Table

from analytics.frequency import muscle_group_frequency, workout_frequency
from analytics.progression import detect_plateaus, exercise_progression, top_progressions
from analytics.records import all_time_records, body_measurement_trend, compute_bmi, recent_prs
from analytics.volume import muscle_group_summary, sets_per_muscle_per_week
from commands._shared import _dlog
from commands.goals import _render_goals_progress
from db.goals import get_height_cm, get_pref
from db.store import query
from i18n import _
from ui.console import STYLE, console
from ui.format import fmt_weight as _fmt_weight
from ui.prompts import week_choices as _week_choices


def _do_stats() -> None:
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


def _do_progress() -> None:
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


def _do_records() -> None:
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
