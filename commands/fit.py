"""Google Fit — connect/sync/dashboard menu actions and recovery panel."""

import json
from pathlib import Path

import questionary
from rich import box
from rich.markup import escape as _esc
from rich.panel import Panel
from rich.table import Table

from commands._shared import _dlog
from i18n import _
from ui.console import STYLE, console
from ui.prompts import confirm_destructive
from ui.prompts import day_choices as _day_choices


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


def _do_fit() -> None:
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
            t.add_row(_("fit.avg_sleep"), _("fit.value_sleep", hours=sleep["avg_hours"]))
            t.add_row(_("fit.last_night"), _("fit.value_hours", hours=sleep.get("last_night_hours")))
            t.add_row(_("fit.nights_7plus"), f"{sleep['nights_7plus_hours']}/{sleep['nights_tracked']}")
        if activity.get("avg_steps"):
            t.add_row(_("fit.avg_steps"), f"{int(activity['avg_steps']):,}")
        if activity.get("avg_calories"):
            t.add_row(_("fit.avg_calories"), _("fit.value_calories", calories=f"{int(activity['avg_calories']):,}"))
        if activity.get("resting_hr"):
            t.add_row(_("fit.resting_hr"), _("fit.value_bpm", rhr=activity["resting_hr"]))
        if activity.get("avg_active_minutes"):
            t.add_row(_("fit.avg_active_minutes"), str(int(activity["avg_active_minutes"])))
        console.print(t)
