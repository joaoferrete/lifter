"""hevy — interactive personal Hevy workout client."""

import sqlite3
from datetime import UTC, datetime

import questionary
from rich.markup import escape as _esc
from rich.panel import Panel

import config
from analytics.frequency import workout_frequency
from analytics.records import body_measurement_trend
from analytics.volume import sets_per_muscle_per_week
from commands._shared import NO_PAUSE, _dlog
from commands.body import _check_body_checkin, _do_body_entry
from commands.coach import _do_chat, _do_coach
from commands.fit import _do_fit
from commands.goals import _do_goals
from commands.profiles import _bootstrap_profiles
from commands.settings import _do_settings
from commands.startup import (
    _check_auto_report,
    _check_goal_celebrations,
    _check_goals_and_checkin,
    _check_stale_sync,
)
from commands.stats import _do_progress, _do_records, _do_stats
from commands.sync import _do_sync

# AI_PROVIDER/AI_MODEL are read as config.X — apply_ai_overrides() mutates them
# at runtime, so an import-by-name here would go stale.
from config import get_provider_api_key
from db.goals import get_goals, get_pref, set_pref
from db.store import header_counts, init_db
from hevy.client import HevyClient
from i18n import _
from ui.console import STYLE, console
from ui.console import score_color as _score_color
from ui.format import fmt_weight as _fmt_weight
from ui.format import get_int_pref
from ui.format import time_ago as _time_ago
from ui.widgets import score_bar as _fmt_score_bar

__all__ = ["NO_PAUSE", "_dlog"]  # re-exported from commands._shared

# ── helpers ───────────────────────────────────────────────────────────────────


def _app_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("lifter-cli")  # distribution name, not a module name
    except PackageNotFoundError:
        return "dev"


def _sync_status_str(key: str) -> str:
    """One-line summary of the last recorded sync outcome for the dev panel."""
    from db.store import get_sync_result

    result = get_sync_result(key)
    if not result:
        return _("settings.dev.sync_never")
    ago = _time_ago(result.get("when", ""))
    detail = _esc(result.get("detail", ""))
    if result.get("ok"):
        return _("settings.dev.sync_ok", ago=ago, detail=detail)
    return _("settings.dev.sync_failed", ago=ago, detail=detail)


def _require_hevy() -> HevyClient | None:
    if not config.HEVY_API_KEY:
        console.print(_("error.hevy_api_key_not_set"))
        return None
    return HevyClient()


def _is_placeholder_key(value: str) -> bool:
    """True if the key is still a .env.example placeholder (e.g. 'sk-or-your-key')."""
    return "your-" in value.lower()


def _provider_key_ok(provider: str) -> bool:
    """Does this provider have a usable credential configured in .env?"""
    if provider == "bedrock":
        return bool(config.AWS_BEARER_TOKEN_BEDROCK or (config.AWS_ACCESS_KEY_ID and config.AWS_SECRET_ACCESS_KEY))
    key = get_provider_api_key(provider)
    return bool(key) and not _is_placeholder_key(key)


def _ai_configured() -> bool:
    """Silent check: is the current AI provider usable? (prints nothing)."""
    return config.AI_PROVIDER in config.KNOWN_PROVIDERS and _provider_key_ok(config.AI_PROVIDER)


def _require_ai() -> bool:
    if config.AI_PROVIDER not in config.KNOWN_PROVIDERS:
        valid = ", ".join(sorted(config.KNOWN_PROVIDERS))
        console.print(_("error.ai_provider_unknown", provider=config.AI_PROVIDER, valid=valid))
        return False
    if config.AI_PROVIDER == "bedrock":
        # Either a bearer token (no boto3 needed) or AWS access keys.
        if _provider_key_ok("bedrock"):
            return True
        console.print(_("error.ai_bedrock_no_creds"))
        return False
    key = get_provider_api_key()
    if not key or _is_placeholder_key(key):
        key_names = {
            "gemini": "GEMINI_API_KEY",
            "claude": "ANTHROPIC_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "groq": "GROQ_API_KEY",
            "github": "GITHUB_TOKEN",
        }
        var = key_names.get(config.AI_PROVIDER, "the relevant API key")
        console.print(_("error.ai_key_not_set", var=var, provider=config.AI_PROVIDER))
        return False
    return True


def _pause():
    from ui.prompts import pause

    pause()


# ── unit helpers ──────────────────────────────────────────────────────────────


def _report_weeks() -> int:
    """Weeks of history for coaching reports (pref `report_weeks`)."""
    return get_int_pref("report_weeks", 8, allowed=(4, 8, 12))


def _stale_seconds() -> int:
    """Age after which synced data counts as stale (pref `sync_stale_hours`)."""
    return get_int_pref("sync_stale_hours", 24, minimum=1) * 3600


# ── score & muscle-distribution helpers ──────────────────────────────────────

_MUSCLE_GROUPS: dict = {
    "Chest": ["chest", "pectorals"],
    "Back": ["lats", "upper_back", "lower_back", "trapezius"],
    "Legs": ["quadriceps", "hamstrings", "glutes", "calves", "hip_flexors"],
    "Shoulders": ["shoulders", "deltoids"],
    "Arms": ["biceps", "triceps", "forearms"],
    "Core": ["abdominals", "core", "obliques"],
    "Cardio": ["cardio", "full_body"],
}


def _sets_by_group(weeks: int = 4) -> dict:
    from render_cache import cached

    return cached(f"sets_by_group:{weeks}", lambda: _sets_by_group_uncached(weeks))


def _sets_by_group_uncached(weeks: int = 4) -> dict:
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
            score_lines.append(_fmt_score_bar(_("score.training"), int(ws_raw)))
        if hs_raw:
            score_lines.append(_fmt_score_bar(_("score.health"), int(hs_raw)))
        if cs_raw:
            score_lines.append(_fmt_score_bar(_("score.overall"), int(cs_raw)))
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

    # Body: latest weight + BMI (when height is known)
    from analytics.records import compute_bmi
    from db.goals import get_height_cm

    body = body_measurement_trend(8)
    if body.get("weight_kg"):
        body_parts = [f"[bold]{_fmt_weight(body['weight_kg'])}[/bold]"]
        if body.get("fat_percent"):
            body_parts.append(f"{body['fat_percent']}% {_('snapshot.body_fat_short')}")
        bmi = compute_bmi(body.get("weight_kg"), get_height_cm())
        if bmi is not None:
            body_parts.append(f"{_('snapshot.bmi_short')} {bmi}")
        lines.append(_("snapshot.body_title"))
        lines.append("  " + "  ·  ".join(body_parts))
        lines.append("")

    # Compact goal progress
    from analytics.goal_progress import compute_goal_progress
    from commands.goals import describe_goal as _describe_goal

    progress = compute_goal_progress()
    numeric = [g for g in progress if g.get("pct") is not None and not g["achieved"]]
    achieved = [g for g in progress if g["achieved"]]
    if numeric or achieved:
        lines.append(_("snapshot.goals_title"))
        for g in numeric[:4]:
            pct = float(g["pct"])
            color = _score_color(max(0, int(pct)))
            bw = max(0, min(8, int(pct / 100 * 8)))
            bar = f"[{color}]{'█' * bw}[/{color}][dim]{'░' * (8 - bw)}[/dim]"
            desc = _describe_goal(g)[:30]
            lines.append(f"  {bar} [{color}]{pct:.0f}%[/{color}]  [dim]{desc}[/dim]")
        if len(numeric) > 4:
            lines.append(_("snapshot.n_more_goals", count=len(numeric) - 4))
        for g in achieved[:2]:
            lines.append(f"  [bold green]✓[/bold green] [dim]{_describe_goal(g)[:35]}[/dim]")
        custom = [g for g in progress if g.get("pct") is None and not g["achieved"]]
        for g in custom[:2]:
            lines.append(f"  [dim]◦ {_describe_goal(g)[:35]} {_('goals.custom_label')}[/dim]")

    if not lines:
        return

    console.print(
        Panel(
            "\n".join(lines).strip(),
            title=_("snapshot.panel_title"),
            border_style="dim",
            padding=(0, 2),
        )
    )
    console.print()


# ── header ────────────────────────────────────────────────────────────────────


def _show_header() -> None:
    from db.store import get_sync_state

    last_sync = get_sync_state("last_sync")
    counts = header_counts()
    total = counts["workouts"]
    week_count = counts["week_workouts"]
    freq = workout_frequency(4)
    goals = get_goals()
    name = get_pref("display_name")

    # Last workout
    last_workout_at = counts["last_workout_at"]
    lw_str = _("header.last_workout", ago=_time_ago(last_workout_at)) if last_workout_at else _("header.no_workouts")

    # Streak
    streak = freq.get("longest_streak_days", 0)
    streak_parts = []
    if streak >= 2:
        fires = "🔥" * min(streak, 5)
        streak_parts.append(_("header.streak", fires=fires, days=streak))

    # Routines count
    routine_count = counts["routines"]
    routines_str = _("header.routines_plural" if routine_count != 1 else "header.routines", count=routine_count)

    # Sync status
    if last_sync:
        try:
            secs = int((datetime.now(UTC) - datetime.fromisoformat(last_sync.replace("Z", "+00:00"))).total_seconds())
            sync_str = (
                _("header.sync_ok", ago=_time_ago(last_sync))
                if secs < _stale_seconds()
                else _("header.sync_stale", ago=_time_ago(last_sync))
            )
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
    line1_parts = [lw_str, *streak_parts, routines_str]
    line1 = "  ·  ".join(line1_parts)

    line2 = _("header.line2", total=total, week=week_count, avg=freq["avg_per_week"])
    if goals:
        goals_str = _("header.goals_plural" if len(goals) != 1 else "header.goals", count=len(goals))
        line2 += f"  ·  {goals_str}"

    line3 = f"[dim]{ai_str}  ·  {sync_str}{recovery_str}[/dim]"

    brand = f"LIFTER [dim]v{_app_version()}[/dim]"
    title = f"[bold cyan]{brand}  [dim]·[/dim]  {_esc(name)}[/bold cyan]" if name else f"[bold cyan]{brand}[/bold cyan]"
    console.print(
        Panel(
            f"{line1}\n{line2}\n{line3}",
            title=title,
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()


# ── main loop ─────────────────────────────────────────────────────────────────

ACTIONS = {
    "sync": _do_sync,
    "stats": _do_stats,
    "progress": _do_progress,
    "records": _do_records,
    "goals": _do_goals,
    "body": _do_body_entry,
    "fit": _do_fit,
    "coach": _do_coach,
    "chat": _do_chat,
    "settings": _do_settings,
}


def _run_action(choice: str, action) -> object:
    """Run a menu action behind the app-wide safety net.

    Error-handling convention: RuntimeError carries a user-ready message
    (raised by the Hevy/Fit/AI layers); anything else is a bug — shown as a
    generic panel and recorded with a full traceback via debug_log.error().
    Returns the action's result (NO_PAUSE or None).
    """
    import debug_log

    try:
        return action()
    except (KeyboardInterrupt, EOFError):
        return None
    except RuntimeError as e:
        debug_log.error("APP", f"Action '{choice}' failed", exc=e)
        console.print(f"[red]{_esc(str(e))}[/red]")
        return None
    except Exception as e:
        debug_log.error("APP", f"Unhandled error in '{choice}'", exc=e)
        hint = ""
        if isinstance(e, sqlite3.OperationalError) and any(word in str(e).lower() for word in ("locked", "malformed")):
            hint = "\n" + _("error.db_hint")
        console.print(
            Panel(
                _("error.unexpected", exc_type=type(e).__name__, log_dir=_esc(str(debug_log.logs_dir()))) + hint,
                border_style="red",
                padding=(0, 2),
            )
        )
        return None


def _build_menu() -> tuple:
    try:
        from fit.auth import is_connected as _fit_connected

        fit_label = _("menu.fit_connected") if _fit_connected() else _("menu.fit_disconnected")
    except Exception:
        fit_label = _("menu.fit_connected")

    last_action = get_pref("last_menu_action")
    items = [
        questionary.Choice(_("menu.sync"), value="sync"),
        questionary.Choice(_("menu.chat"), value="chat"),
        questionary.Separator("  ──────────────────────────────────"),
        questionary.Choice(_("menu.goals"), value="goals"),
        questionary.Choice(_("menu.body"), value="body"),
        questionary.Choice(_("menu.stats"), value="stats"),
        questionary.Choice(_("menu.progress"), value="progress"),
        questionary.Choice(_("menu.records"), value="records"),
        questionary.Separator("  ──────────────────────────────────"),
        questionary.Choice(_("menu.coach"), value="coach"),
        questionary.Choice(fit_label, value="fit"),
        questionary.Separator("  ──────────────────────────────────"),
        questionary.Choice(_("menu.settings"), value="settings"),
        questionary.Choice(_("menu.exit"), value="exit"),
    ]
    default = next((c for c in items if isinstance(c, questionary.Choice) and c.value == last_action), None)
    return items, default


def main():
    import sys

    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        print(f"lifter {_app_version()}")
        return

    import paths

    paths.ensure_dirs()
    moved = paths.migrate_legacy_layout()
    if moved:
        config.reload_env()  # the .env file may have just moved

    import i18n as _i18n

    _i18n.init(config.DEFAULT_LANGUAGE)  # Phase 1: before profile selector

    if moved:
        console.print(
            Panel(
                "\n".join(_esc(m) for m in moved),
                title=_("migration.xdg_title"),
                border_style="cyan",
                padding=(0, 2),
            )
        )
        console.print(
            _("migration.xdg_done", config_dir=_esc(str(paths.CONFIG_DIR)), data_dir=_esc(str(paths.DATA_DIR)))
        )
    _bootstrap_profiles()
    init_db()
    config.apply_ai_overrides()  # per-profile provider/model prefs
    ui_lang = get_pref("ui_language") or config.DEFAULT_LANGUAGE
    _i18n.init(ui_lang)  # Phase 2: after profile DB is open
    import debug_log

    debug_log.init()
    import config as _cfg
    from profile_mgr import get_active_slug

    _dlog("APP", "Lifter started", profile=get_active_slug() or "none", provider=_cfg.AI_PROVIDER, model=_cfg.AI_MODEL)
    from db.goals import maybe_rollover_tokens

    maybe_rollover_tokens()  # reset monthly token counter if the period rolled over
    try:
        _check_goals_and_checkin()
        _check_body_checkin()
        _check_stale_sync()
        _check_goal_celebrations()
        _check_auto_report()
    except (KeyboardInterrupt, EOFError):
        pass
    except Exception as e:
        # A broken startup check must never keep the user from the menu.
        debug_log.error("APP", "Startup check failed", exc=e)
        console.print(_("error.startup_check_failed", exc_type=type(e).__name__))

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
        result = _run_action(choice, action) if action else None

        if result is not NO_PAUSE:
            _pause()


if __name__ == "__main__":
    main()
