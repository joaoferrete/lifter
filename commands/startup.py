"""Startup checks — stale-sync prompt, goals check-in, celebrations, auto-report."""

from datetime import UTC, datetime

import questionary
from rich.markup import escape as _esc
from rich.panel import Panel

import config
from analytics.goal_progress import compute_goal_progress
from commands._shared import _dlog
from commands.coach import _run_report
from commands.goals import _weekly_checkin, run_goals_wizard
from db.goals import (
    get_goals,
    get_pref,
    get_uncelebrated_achievements,
    mark_achievements_celebrated,
    should_ask_goals,
    should_auto_report,
)
from db.store import get_sync_state, query
from hevy.sync import incremental_sync
from i18n import _
from ui.console import STYLE, console


def _check_stale_sync() -> None:
    """Auto-sync or prompt if Hevy/Google Fit data is older than 24 hours."""
    from cli import _require_hevy, _stale_seconds

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
    from cli import _pause

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
    from cli import _ai_configured, _pause, _report_weeks

    if not should_auto_report():
        return
    # Silent AI check — never nag at startup when AI isn't configured.
    if not _ai_configured():
        return
    # Nothing to report on yet — skip until there's training data.
    if not query("SELECT 1 FROM workouts LIMIT 1"):
        return

    console.print()
    console.print(_("coach.auto_report_intro"))
    _dlog("AI", "Auto coaching report triggered (7-day)")
    # Analysis only — creating a routine stays an explicit user action.
    if _run_report(weeks=_report_weeks(), generate_routine=False):
        _pause()
