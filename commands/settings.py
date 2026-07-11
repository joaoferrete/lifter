"""Settings — AI/provider config, preferences, memories, data reset, export/import, developer tools."""

import contextlib
import sqlite3
from datetime import datetime
from pathlib import Path

import questionary
from rich.markup import escape as _esc
from rich.panel import Panel

import config
import debug_log
from commands._shared import _UI_LANGUAGES, _dlog
from commands.profiles import _do_profile_settings, _do_profiles_menu
from db.export import export_data, import_data, read_import_payload
from db.goals import (
    get_pref,
    get_token_budget,
    get_token_reset_day,
    get_token_usage,
    get_token_usage_month,
    reset_token_usage,
    set_pref,
    set_token_budget,
    set_token_reset_day,
    token_budget_status,
)
from db.memories import count_memories, delete_memories, enforce_memory_cap, get_all_memories
from db.store import init_db, query
from i18n import _
from ui.console import STYLE, console
from ui.format import get_units as _get_units
from ui.prompts import confirm_destructive
from ui.prompts import week_choices as _week_choices

_AI_LANGUAGES = [
    "English",
    "Portuguese (BR)",
    "Portuguese (PT)",
    "Spanish",
    "French",
    "German",
    "Italian",
    "Dutch",
    "Polish",
    "Russian",
    "Japanese",
    "Chinese",
]


# (env var, i18n label key, hidden input)
_ENV_KEY_FIELDS: list[tuple[str, str, bool]] = [
    ("GEMINI_API_KEY", "settings.keys.gemini", True),
    ("ANTHROPIC_API_KEY", "settings.keys.claude", True),
    ("OPENROUTER_API_KEY", "settings.keys.openrouter", True),
    ("GROQ_API_KEY", "settings.keys.groq", True),
    ("GITHUB_TOKEN", "settings.keys.github", True),
    ("AWS_BEARER_TOKEN_BEDROCK", "settings.keys.bedrock_token", True),
    ("AWS_REGION", "settings.keys.aws_region", False),
    ("AWS_ACCESS_KEY_ID", "settings.keys.aws_access", True),
    ("AWS_SECRET_ACCESS_KEY", "settings.keys.aws_secret", True),
    ("AWS_SESSION_TOKEN", "settings.keys.aws_session", True),
]


def _mask_secret(value: str) -> str:
    from cli import _is_placeholder_key

    if not value or _is_placeholder_key(value):
        return _("settings.keys.not_set")
    return f"…{value[-4:]}" if len(value) > 4 else "…"


def _do_api_keys_settings() -> None:
    """Edit provider credentials in-app — persisted to the global .env file."""
    import paths

    while True:
        console.clear()
        lines = [_("settings.keys.env_file_line", path=_esc(str(paths.ENV_FILE))), ""]
        for var, label_key, _hidden in _ENV_KEY_FIELDS:
            lines.append(f"{_(label_key)}: [bold]{_esc(_mask_secret(getattr(config, var, '')))}[/bold]")
        console.print(Panel("\n".join(lines), title=_("settings.keys.title"), border_style="cyan", padding=(0, 2)))

        choices = [
            questionary.Choice(f"  {_(label_key)}  ({_mask_secret(getattr(config, var, ''))})", value=var)
            for var, label_key, _hidden in _ENV_KEY_FIELDS
        ]
        choices += [questionary.Separator("  ───"), questionary.Choice(_("nav.back"), value="back")]
        picked = questionary.select(_("settings.keys.prompt"), choices=choices, style=STYLE).ask()
        if not picked or picked == "back":
            return

        hidden = next(h for var, _k, h in _ENV_KEY_FIELDS if var == picked)
        action = questionary.select(
            _("settings.keys.action_prompt", field=picked, current=_mask_secret(getattr(config, picked, ""))),
            choices=[
                questionary.Choice(_("settings.keys.set_option"), value="set"),
                questionary.Choice(_("settings.keys.clear_option"), value="clear"),
                questionary.Choice(_("nav.cancel"), value=None),
            ],
            style=STYLE,
        ).ask()
        if not action:
            continue

        if action == "set":
            asker = questionary.password if hidden else questionary.text
            value = asker(_("settings.keys.value_prompt"), style=STYLE).ask()
            if not value or not value.strip():
                continue
            new_value = value.strip()
        else:
            new_value = ""  # written as KEY= so reload_env(override=True) clears it

        config.set_env_values({picked: new_value})
        config.reload_env()
        config.apply_ai_overrides()  # profile prefs keep winning over fresh env
        _dlog("SETTING", "env key updated", var=picked, cleared=not new_value)
        if new_value:
            console.print(_("settings.keys.saved", var=picked))
        else:
            console.print(_("settings.keys.cleared", var=picked))


def _do_ai_settings() -> None:
    from cli import _provider_key_ok, _report_weeks

    def _token_block(title: str, usage: dict) -> list[str]:
        total = usage["input"] + usage["output"]
        cache_pct = int(usage["cache_read"] / usage["input"] * 100) if usage["input"] else 0
        block = [
            title,
            _("settings.ai.tokens_input", count=f"{usage['input']:,}"),
            _("settings.ai.tokens_output", count=f"{usage['output']:,}"),
            _("settings.ai.tokens_total", count=f"{total:,}"),
        ]
        if usage["cache_read"]:
            block.append(_("settings.ai.tokens_cached", count=f"{usage['cache_read']:,}", pct=cache_pct))
        return block

    while True:
        console.clear()
        usage_month = get_token_usage_month()
        usage_total = get_token_usage()
        reset_day = get_token_reset_day()
        try:
            history_turns = max(0, int(get_pref("ai_chat_history_turns") or 12))
        except (TypeError, ValueError):
            history_turns = 12
        history_label = (
            _("settings.ai.history_unlimited")
            if history_turns == 0
            else _("settings.ai.history_count", n=history_turns)
        )
        slim_on = get_pref("ai_chat_slim") != "0"
        routines_on = get_pref("ai_include_routines") != "0"
        auto_report_on = get_pref("auto_report") != "0"
        send_name_on = get_pref("ai_send_name") != "0"
        send_body_on = get_pref("ai_send_body") != "0"
        report_weeks = _report_weeks()
        budget_status = token_budget_status()
        mem_count = count_memories()
        lang = get_pref("ai_language") or "English"
        if lang == "Portuguese":
            lang = "Portuguese (BR)"
            set_pref("ai_language", lang)

        slim_label = _("settings.ai.context_slim") if slim_on else _("settings.ai.context_full")

        def on_off(b: bool) -> str:
            return _("settings.ai.on") if b else _("settings.ai.off")

        budget_label = (
            _("settings.ai.budget_off")
            if budget_status is None
            else _("settings.ai.budget_usage", budget=f"{budget_status['budget']:,}", pct=int(budget_status["pct"]))
        )

        lines = [
            _("settings.ai.provider_line", provider=config.AI_PROVIDER, model=config.AI_MODEL),
            _("settings.ai.context_line", mode=slim_label),
            _("settings.ai.routines_line", state=on_off(routines_on)),
            _("settings.ai.auto_report_line", state=on_off(auto_report_on)),
            _("settings.ai.report_weeks_line", weeks=report_weeks),
            _("settings.ai.privacy_name_line", state=on_off(send_name_on)),
            _("settings.ai.privacy_body_line", state=on_off(send_body_on)),
            _("settings.ai.language_line", lang=lang),
            _("settings.ai.history_turns_line", turns=history_label),
            _("settings.ai.reset_day_line", day=reset_day),
            _("settings.ai.budget_line", budget=budget_label),
        ]
        if budget_status and budget_status["pct"] >= 100:
            lines.append(
                _(
                    "settings.ai.budget_exceeded",
                    used=f"{budget_status['used']:,}",
                    budget=f"{budget_status['budget']:,}",
                )
            )
        elif budget_status and budget_status["pct"] >= 80:
            lines.append(
                _(
                    "settings.ai.budget_warning",
                    pct=int(budget_status["pct"]),
                    used=f"{budget_status['used']:,}",
                    budget=f"{budget_status['budget']:,}",
                )
            )
        lines.append("")
        lines += _token_block(_("settings.ai.token_usage_month_title"), usage_month)
        lines.append("")
        lines += _token_block(_("settings.ai.token_usage_total_title"), usage_total)

        console.print(Panel("\n".join(lines), title=_("settings.ai.title"), border_style="cyan"))

        action = questionary.select(
            _("settings.ai.prompt"),
            choices=[
                questionary.Choice(_("settings.ai.provider_choice", provider=config.AI_PROVIDER), value="provider"),
                questionary.Choice(_("settings.ai.model_choice", model=config.AI_MODEL), value="model"),
                questionary.Choice(_("settings.ai.api_keys_choice"), value="api_keys"),
                questionary.Choice(
                    _(
                        "settings.ai.toggle_context_choice",
                        mode=_("settings.ai.context_mode_slim") if slim_on else _("settings.ai.context_mode_full"),
                    ),
                    value="toggle_slim",
                ),
                questionary.Choice(
                    _("settings.ai.toggle_routines_choice", state=on_off(routines_on)),
                    value="toggle_routines",
                ),
                questionary.Choice(
                    _("settings.ai.toggle_auto_report_choice", state=on_off(auto_report_on)),
                    value="toggle_auto_report",
                ),
                questionary.Choice(_("settings.ai.report_weeks_choice", weeks=report_weeks), value="report_weeks"),
                questionary.Choice(
                    _("settings.ai.toggle_name_choice", state=on_off(send_name_on)), value="toggle_name"
                ),
                questionary.Choice(
                    _("settings.ai.toggle_body_choice", state=on_off(send_body_on)), value="toggle_body"
                ),
                questionary.Choice(_("settings.ai.language_choice", lang=lang), value="language"),
                questionary.Choice(_("settings.ai.history_turns_choice", turns=history_label), value="history_turns"),
                questionary.Choice(_("settings.ai.memories_choice", count=mem_count), value="memories"),
                questionary.Choice(_("settings.ai.budget_choice", budget=budget_label), value="budget"),
                questionary.Choice(_("settings.ai.reset_day_choice", day=reset_day), value="reset_day"),
                questionary.Choice(_("settings.ai.reset_tokens_choice"), value="reset_tokens"),
                questionary.Separator("  ───"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "provider":
            configured = [p for p in sorted(config.KNOWN_PROVIDERS) if _provider_key_ok(p)]
            if not configured:
                console.print(_("settings.ai.provider_none"))
                continue
            choices = [questionary.Choice(f"{p}  ·  {config.default_model_for(p)}", value=p) for p in configured]
            choices.append(
                questionary.Choice(_("settings.ai.provider_env_option", provider=config._ENV_AI_PROVIDER), value="_env")
            )
            choices.append(questionary.Choice(_("nav.cancel"), value=None))
            picked = questionary.select(_("settings.ai.provider_prompt"), choices=choices, style=STYLE).ask()
            if picked == "_env":
                set_pref("ai_provider", "")
                set_pref("ai_model", "")
            elif picked and picked != config.AI_PROVIDER:
                set_pref("ai_provider", picked)
                set_pref("ai_model", "")  # model names are provider-specific
            if picked:
                config.apply_ai_overrides()
                _dlog("SETTING", "ai_provider changed", provider=config.AI_PROVIDER, model=config.AI_MODEL)
                console.print(_("settings.ai.provider_saved", provider=config.AI_PROVIDER, model=config.AI_MODEL))
            continue

        if action == "model":
            default_model = config.default_model_for(config.AI_PROVIDER)
            picked = questionary.select(
                _("settings.ai.model_prompt", provider=config.AI_PROVIDER),
                choices=[
                    questionary.Choice(_("settings.ai.model_default_option", model=default_model), value=default_model),
                    questionary.Choice(_("settings.ai.model_custom_option"), value="_custom"),
                    questionary.Choice(_("nav.cancel"), value=None),
                ],
                style=STYLE,
            ).ask()
            if picked == "_custom":
                picked = questionary.text(
                    _("settings.ai.model_custom_prompt"),
                    default=config.AI_MODEL,
                    validate=lambda v: bool(v.strip()) or _("settings.ai.model_invalid"),
                    style=STYLE,
                ).ask()
                picked = picked.strip() if picked else None
            if picked:
                set_pref("ai_model", picked)
                config.apply_ai_overrides()
                _dlog("SETTING", "ai_model changed", model=config.AI_MODEL)
                console.print(_("settings.ai.model_saved", model=config.AI_MODEL))
            continue

        if action == "api_keys":
            _do_api_keys_settings()
            continue

        if action == "toggle_slim":
            new_val = "0" if slim_on else "1"
            set_pref("ai_chat_slim", new_val)
            label = _("settings.ai.context_slim") if new_val == "1" else _("settings.ai.context_full")
            _dlog("SETTING", "ai_chat_slim changed", value=label)
            console.print(_("settings.ai.context_saved", mode=label))

        elif action == "toggle_routines":
            new_val = "0" if routines_on else "1"
            set_pref("ai_include_routines", new_val)
            _dlog("SETTING", "ai_include_routines changed", value=new_val)
            console.print(_("settings.ai.routines_saved", state=on_off(new_val != "0")))

        elif action == "toggle_auto_report":
            new_val = "0" if auto_report_on else "1"
            set_pref("auto_report", new_val)
            _dlog("SETTING", "auto_report changed", value=new_val)
            console.print(_("settings.ai.auto_report_saved", state=on_off(new_val != "0")))

        elif action == "report_weeks":
            answer = questionary.select(
                _("settings.ai.report_weeks_prompt"),
                choices=_week_choices([4, 8, 12]),
                default=f"{report_weeks} weeks",
                style=STYLE,
            ).ask()
            if answer:
                new_weeks = int(answer.split()[0])
                set_pref("report_weeks", str(new_weeks))
                _dlog("SETTING", "report_weeks changed", value=new_weeks)
                console.print(_("settings.ai.report_weeks_saved", weeks=new_weeks))

        elif action == "toggle_name":
            new_val = "0" if send_name_on else "1"
            set_pref("ai_send_name", new_val)
            _dlog("SETTING", "ai_send_name changed", value=new_val)
            console.print(_("settings.ai.privacy_name_saved", state=on_off(new_val != "0")))

        elif action == "toggle_body":
            new_val = "0" if send_body_on else "1"
            set_pref("ai_send_body", new_val)
            _dlog("SETTING", "ai_send_body changed", value=new_val)
            console.print(_("settings.ai.privacy_body_saved", state=on_off(new_val != "0")))

        elif action == "memories":
            _do_manage_memories()

        elif action == "budget":
            answer = questionary.text(
                _("settings.ai.budget_prompt"),
                default=str(get_token_budget()),
                validate=lambda v: v.isdigit() or _("settings.ai.budget_invalid"),
                style=STYLE,
            ).ask()
            if answer is not None and answer.isdigit():
                set_token_budget(int(answer))
                _dlog("SETTING", "ai_tokens_month_budget changed", value=int(answer))
                console.print(_("settings.ai.budget_saved", budget=f"{int(answer):,}"))

        elif action == "language":
            lang_choices = _AI_LANGUAGES + ([] if lang in _AI_LANGUAGES else [lang])
            new_lang = questionary.select(
                _("settings.ai.language_prompt"),
                choices=lang_choices,
                default=lang if lang in lang_choices else lang_choices[0],
                style=STYLE,
            ).ask()
            if new_lang:
                set_pref("ai_language", new_lang)
                _dlog("SETTING", "ai_language changed", value=new_lang)
                console.print(_("settings.ai.language_saved", lang=new_lang))

        elif action == "history_turns":
            answer = questionary.text(
                _("settings.ai.history_turns_prompt"),
                default=str(history_turns),
                validate=lambda v: (v.isdigit() and 0 <= int(v) <= 100) or _("settings.ai.history_turns_invalid"),
                style=STYLE,
            ).ask()
            if answer is not None and answer.isdigit():
                n = int(answer)
                set_pref("ai_chat_history_turns", str(n))
                _dlog("SETTING", "ai_chat_history_turns changed", value=n)
                saved_label = _("settings.ai.history_unlimited") if n == 0 else _("settings.ai.history_count", n=n)
                console.print(_("settings.ai.history_turns_saved", turns=saved_label))

        elif action == "reset_day":
            answer = questionary.text(
                _("settings.ai.reset_day_prompt"),
                default=str(reset_day),
                validate=lambda v: (v.isdigit() and 1 <= int(v) <= 31) or _("settings.ai.reset_day_invalid"),
                style=STYLE,
            ).ask()
            if answer:
                set_token_reset_day(int(answer))
                _dlog("SETTING", "ai_tokens_reset_day changed", value=answer)
                console.print(_("settings.ai.reset_day_saved", day=int(answer)))

        elif action == "reset_tokens":
            if questionary.confirm(_("settings.ai.reset_tokens_prompt"), default=False, style=STYLE).ask():
                reset_token_usage()
                _dlog("SETTING", "token counters reset (month + lifetime)")
                console.print(_("settings.ai.reset_tokens_done"))


def _do_manage_memories() -> None:
    while True:
        console.clear()
        try:
            mem_max = max(0, int(get_pref("memories_max") or 200))
        except (TypeError, ValueError):
            mem_max = 200
        max_label = _("settings.ai.memories_unlimited") if mem_max == 0 else str(mem_max)
        console.print(
            Panel(
                _("settings.ai.memories_panel", count=count_memories(), max=max_label),
                title=_("settings.ai.memories_title"),
                border_style="cyan",
                padding=(0, 2),
            )
        )

        action = questionary.select(
            _("settings.ai.memories_prompt"),
            choices=[
                questionary.Choice(_("settings.ai.memories_delete_choice"), value="delete"),
                questionary.Choice(_("settings.ai.memories_limit_choice", max=max_label), value="limit"),
                questionary.Separator("  ───"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "delete":
            memories = get_all_memories()
            if not memories:
                console.print(_("settings.ai.memories_none"))
                questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()
                continue
            choices = [
                questionary.Choice(f"[{(m['created_at'] or '')[:10]}] {m['summary'][:70]}", value=m["id"])
                for m in memories
            ]
            picked = questionary.checkbox(_("settings.ai.memories_select"), choices=choices, style=STYLE).ask()
            if not picked:
                continue
            if questionary.confirm(
                _("settings.ai.memories_delete_confirm", count=len(picked)), default=False, style=STYLE
            ).ask():
                deleted = delete_memories(picked)
                _dlog("SETTING", "memories deleted", count=deleted)
                console.print(_("settings.ai.memories_deleted", count=deleted))
                questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()

        elif action == "limit":
            answer = questionary.text(
                _("settings.ai.memories_limit_prompt"),
                default=str(mem_max),
                validate=lambda v: (v.isdigit() and 0 <= int(v) <= 10000) or _("settings.ai.memories_limit_invalid"),
                style=STYLE,
            ).ask()
            if answer is not None and answer.isdigit():
                new_max = int(answer)
                set_pref("memories_max", str(new_max))
                enforce_memory_cap()
                _dlog("SETTING", "memories_max changed", value=new_max)
                saved_label = _("settings.ai.memories_unlimited") if new_max == 0 else str(new_max)
                console.print(_("settings.ai.memories_limit_saved", max=saved_label))


def _do_data_reset() -> None:
    while True:
        console.clear()
        action = questionary.select(
            _("data_reset.prompt"),
            choices=[
                questionary.Choice(_("data_reset.memories_choice"), value="memories"),
                questionary.Choice(_("data_reset.goals_choice"), value="goals"),
                questionary.Choice(_("data_reset.sync_state_choice"), value="sync_state"),
                questionary.Choice(_("data_reset.all_choice"), value="all"),
                questionary.Separator("  ───"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "memories":
            if confirm_destructive(_("data_reset.memories_confirm"), double=True):
                from db.memories import clear_memories

                clear_memories()
                _dlog("RESET", "Coach memories cleared")
                console.print(_("data_reset.memories_done"))

        elif action == "goals":
            if confirm_destructive(_("data_reset.goals_confirm"), double=True):
                from db.goals import clear_goals

                clear_goals()
                _dlog("RESET", "All goals cleared")
                console.print(_("data_reset.goals_done"))

        elif action == "sync_state":
            if questionary.confirm(_("data_reset.sync_state_confirm"), default=False, style=STYLE).ask():
                from db.store import set_sync_state

                set_sync_state("last_sync", "1970-01-01T00:00:00Z")
                _dlog("RESET", "Sync state reset")
                console.print(_("data_reset.sync_state_done"))

        elif action == "all":
            # Spell out exactly what the wipe removes so it's never a surprise.
            def _count(sql: str) -> int:
                try:
                    return query(sql)[0]["n"]
                except Exception:
                    return 0

            summary_lines = [
                _("data_reset.all_item_workouts", n=_count("SELECT COUNT(*) AS n FROM workouts")),
                _("data_reset.all_item_goals", n=_count("SELECT COUNT(*) AS n FROM user_goals")),
                _("data_reset.all_item_measurements", n=_count("SELECT COUNT(*) AS n FROM body_measurements")),
                _("data_reset.all_item_routines", n=_count("SELECT COUNT(*) AS n FROM routines")),
                _("data_reset.all_item_memories", n=_count("SELECT COUNT(*) AS n FROM chat_memories")),
                _("data_reset.all_item_fit", n=_count("SELECT COUNT(*) AS n FROM fit_daily")),
            ]
            console.print(
                Panel(
                    "\n".join(summary_lines),
                    title=_("data_reset.all_summary_title"),
                    border_style="red",
                )
            )
            console.print(_("data_reset.all_warning"))
            if not questionary.confirm(_("data_reset.all_confirm1"), default=False, style=STYLE).ask():
                continue
            if not questionary.confirm(_("data_reset.all_confirm2"), default=False, style=STYLE).ask():
                continue

            import os

            from config import DB_PATH

            try:
                from fit.auth import disconnect as fit_disconnect

                fit_disconnect()
            except Exception:
                pass
            # Remove the WAL/SHM sidecars too — leftover WAL pages still hold
            # wiped data and could be replayed into the recreated database.
            for suffix in ("", "-wal", "-shm"):
                with contextlib.suppress(FileNotFoundError):
                    os.remove(f"{DB_PATH}{suffix}")
            init_db()  # callers up the menu chain read prefs immediately

            from render_cache import invalidate

            invalidate()
            _dlog("RESET", "Full data wipe executed")
            console.print(_("data_reset.all_done"))
            return  # DB is gone — exit all the way back to main


def _do_preferences_settings() -> None:
    import i18n as _i18n
    from cli import _stale_seconds

    while True:
        console.clear()
        units = _get_units()
        checkin_days = int(get_pref("goals_checkin_days") or 7)
        auto_sync = get_pref("auto_sync") == "1"
        default_weeks = get_pref("default_stats_weeks") or "8 weeks"
        stale_hours = _stale_seconds() // 3600
        ui_lang_code = get_pref("ui_language") or config.DEFAULT_LANGUAGE
        ui_lang_name = dict(_UI_LANGUAGES).get(ui_lang_code, ui_lang_code)
        on_str = _("settings.on")
        off_str = _("settings.off")

        lines = [
            _("settings.prefs.units_label", units=units),
            _("settings.prefs.checkin_label", days=checkin_days),
            _("settings.prefs.autosync_label", state=on_str if auto_sync else off_str),
            _("settings.prefs.stale_hours_label", hours=stale_hours),
            _("settings.prefs.stats_window_label", window=default_weeks),
            _("settings.prefs.ui_language_label", lang=ui_lang_name),
            _("settings.prefs.export_dir_label", path=_esc(str(config.export_dir()))),
            _("settings.prefs.logs_dir_label", path=_esc(str(debug_log.logs_dir()))),
        ]
        console.print(Panel("\n".join(lines), title=_("settings.prefs.title"), border_style="cyan", padding=(0, 2)))

        action = questionary.select(
            _("settings.prefs.prompt"),
            choices=[
                questionary.Choice(_("settings.prefs.units_choice", units=units), value="units"),
                questionary.Choice(_("settings.prefs.checkin_choice", days=checkin_days), value="checkin"),
                questionary.Choice(
                    _("settings.prefs.autosync_choice", state=on_str if auto_sync else off_str), value="autosync"
                ),
                questionary.Choice(_("settings.prefs.stale_hours_choice", hours=stale_hours), value="stale_hours"),
                questionary.Choice(_("settings.prefs.stats_window_choice", window=default_weeks), value="stats_window"),
                questionary.Choice(_("settings.prefs.ui_language_choice", lang=ui_lang_name), value="ui_language"),
                questionary.Choice(_("settings.prefs.dirs_choice"), value="dirs"),
                questionary.Separator("  ───"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "units":
            new_units = questionary.select(
                _("settings.prefs.units_prompt"),
                choices=[
                    questionary.Choice(_("settings.prefs.units_kg"), value="kg"),
                    questionary.Choice(_("settings.prefs.units_lbs"), value="lbs"),
                ],
                default=units,
                style=STYLE,
            ).ask()
            if new_units:
                set_pref("units", new_units)
                _dlog("SETTING", "units changed", value=new_units)
                console.print(_("settings.prefs.units_saved", units=new_units))

        elif action == "checkin":
            new_days = questionary.select(
                _("settings.prefs.checkin_prompt"),
                choices=[
                    questionary.Choice(_("settings.prefs.checkin_7"), value="7"),
                    questionary.Choice(_("settings.prefs.checkin_14"), value="14"),
                    questionary.Choice(_("settings.prefs.checkin_30"), value="30"),
                ],
                default=str(checkin_days),
                style=STYLE,
            ).ask()
            if new_days:
                set_pref("goals_checkin_days", new_days)
                _dlog("SETTING", "goals_checkin_days changed", value=new_days)
                console.print(_("settings.prefs.checkin_saved", days=new_days))

        elif action == "autosync":
            new_auto = "0" if auto_sync else "1"
            set_pref("auto_sync", new_auto)
            _dlog("SETTING", "auto_sync changed", value=new_auto)
            console.print(_("settings.prefs.autosync_disabled" if auto_sync else "settings.prefs.autosync_enabled"))

        elif action == "stale_hours":
            answer = questionary.select(
                _("settings.prefs.stale_hours_prompt"),
                choices=[questionary.Choice(_("time.hours", n=h), value=str(h)) for h in (6, 12, 24, 48)],
                default=str(stale_hours) if stale_hours in (6, 12, 24, 48) else "24",
                style=STYLE,
            ).ask()
            if answer:
                set_pref("sync_stale_hours", answer)
                _dlog("SETTING", "sync_stale_hours changed", value=answer)
                console.print(_("settings.prefs.stale_hours_saved", hours=answer))

        elif action == "stats_window":
            new_window = questionary.select(
                _("settings.prefs.stats_window_prompt"),
                choices=_week_choices([4, 8, 12, 24]),
                default=default_weeks,
                style=STYLE,
            ).ask()
            if new_window:
                set_pref("default_stats_weeks", new_window)
                _dlog("SETTING", "default_stats_weeks changed", value=new_window)
                console.print(_("settings.prefs.stats_window_saved", window=new_window))

        elif action == "ui_language":
            lang_choices = [questionary.Choice(name, value=code) for code, name in _UI_LANGUAGES]
            new_code = questionary.select(
                _("settings.prefs.ui_language_prompt"),
                choices=lang_choices,
                style=STYLE,
            ).ask()
            if new_code and new_code != ui_lang_code:
                set_pref("ui_language", new_code)
                _i18n.init(new_code)
                _dlog("SETTING", "ui_language changed", value=new_code)
                new_name = dict(_UI_LANGUAGES).get(new_code, new_code)
                console.print(_("settings.prefs.ui_language_saved", lang=new_name))

        elif action == "dirs":
            _do_dirs_settings()


def _do_dirs_settings() -> None:
    """Configure the export and logs folders (global — stored in .env)."""
    which = questionary.select(
        _("settings.prefs.dirs_prompt"),
        choices=[
            questionary.Choice(
                _("settings.prefs.dirs_export_choice", path=_esc(str(config.export_dir()))), value="EXPORT_DIR"
            ),
            questionary.Choice(
                _("settings.prefs.dirs_logs_choice", path=_esc(str(debug_log.logs_dir()))), value="LOGS_DIR"
            ),
            questionary.Choice(_("nav.back"), value=None),
        ],
        style=STYLE,
    ).ask()
    if not which:
        return

    current = config.EXPORT_DIR if which == "EXPORT_DIR" else config.LOGS_DIR
    raw = questionary.path(
        _("settings.prefs.dirs_path_prompt"),
        default=current,
        style=STYLE,
    ).ask()
    if raw is None:
        return

    value = raw.strip()
    if value:
        target = Path(value).expanduser()
        if not target.is_absolute():
            console.print(_("settings.prefs.dirs_not_absolute"))
            return
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            console.print(f"[red]{_esc(str(e))}[/red]")
            return
        value = str(target)

    config.set_env_values({which: value})
    config.reload_env()
    _dlog("SETTING", f"{which} changed", value=value or "(default)")
    if value:
        console.print(_("settings.prefs.dirs_saved", name=which, path=_esc(value)))
    else:
        console.print(_("settings.prefs.dirs_reset", name=which))


def _do_import_data() -> None:
    exports_dir = config.export_dir()
    candidates = (
        sorted(
            exports_dir.glob("lifter-export-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:15]
        if exports_dir.is_dir()
        else []
    )

    if candidates:
        choices = [questionary.Choice(f"  {p.name}  ({p.stat().st_size / 1024:,.0f} KB)", value=p) for p in candidates]
        choices += [
            questionary.Separator("  ───"),
            questionary.Choice(_("settings.dev.import.manual_choice"), value="_manual"),
            questionary.Choice(_("nav.back"), value=None),
        ]
        picked = questionary.select(_("settings.dev.import.pick_prompt"), choices=choices, style=STYLE).ask()
        if picked is None:
            return
    else:
        console.print(_("settings.dev.import.no_files", dir=_esc(str(exports_dir))))
        picked = "_manual"

    if picked == "_manual":
        raw = questionary.path(_("settings.dev.import.path_prompt"), style=STYLE).ask()
        if not raw or not raw.strip():
            return
        picked = Path(raw.strip()).expanduser()
        if not picked.is_file():
            console.print(_("settings.dev.import.not_found", path=_esc(str(picked))))
            questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()
            return

    try:
        payload = read_import_payload(picked)
    except ValueError as e:
        console.print(_("settings.dev.import.invalid", error=_esc(str(e))))
        questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()
        return

    lines = [
        _(
            "settings.dev.import.summary_meta",
            kind=payload.get("kind", "?"),
            when=_esc(str(payload.get("exported_at", "?"))),
        ),
        "",
    ]
    for table, rows in payload["tables"].items():
        try:
            existing = query(f'SELECT COUNT(*) AS n FROM "{table}"')[0]["n"]
            lines.append(_("settings.dev.import.summary_row", table=table, count=len(rows), existing=existing))
        except sqlite3.OperationalError:
            lines.append(_("settings.dev.import.summary_unknown", table=table))
    lines += ["", _("settings.dev.import.warning")]
    console.print(
        Panel("\n".join(lines), title=_("settings.dev.import.summary_title"), border_style="red", padding=(0, 2))
    )

    if not questionary.confirm(_("settings.dev.import.confirm1"), default=False, style=STYLE).ask():
        return
    if (
        payload.get("kind") == "full"
        and not questionary.confirm(_("settings.dev.import.confirm2"), default=False, style=STYLE).ask()
    ):
        return

    try:
        summary = import_data(picked, payload)
    except Exception as e:
        console.print(_("settings.dev.import.failed", error=_esc(str(e))))
        questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()
        return

    _dlog("IMPORT", "data imported", kind=summary["kind"], rows=summary["total"], path=str(picked))
    console.print(_("settings.dev.import.done", rows=summary["total"], path=_esc(str(picked))))
    if summary["skipped_columns"]:
        n_cols = sum(len(v) for v in summary["skipped_columns"].values())
        console.print(_("settings.dev.import.skipped_cols_note", count=n_cols))

    if summary["kind"] == "full":
        # restored prefs carry process-level state — re-apply them
        import i18n as _i18n

        _i18n.init(get_pref("ui_language") or config.DEFAULT_LANGUAGE)
        debug_log.enable(get_pref("debug_logging") == "1")
        config.apply_ai_overrides()
    questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()


def _do_export_data() -> None:
    while True:
        console.clear()
        action = questionary.select(
            _("settings.dev.export.prompt"),
            choices=[
                questionary.Choice(_("settings.dev.export.memories_choice"), value="memories"),
                questionary.Choice(_("settings.dev.export.goals_choice"), value="goals"),
                questionary.Choice(_("settings.dev.export.measurements_choice"), value="measurements"),
                questionary.Choice(_("settings.dev.export.full_choice"), value="full"),
                questionary.Separator("  ───"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        try:
            path, rows = export_data(action)
        except Exception as e:
            console.print(_("settings.dev.export.failed", error=_esc(str(e))))
            questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()
            continue
        _dlog("EXPORT", "data exported", kind=action, rows=rows)
        console.print(_("settings.dev.export.done", rows=rows, path=_esc(str(path))))
        if rows == 0:
            console.print(_("settings.dev.export.empty_note"))
        questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()


def _do_developer_settings() -> None:
    from cli import _app_version, _sync_status_str

    while True:
        console.clear()
        debug_on = get_pref("debug_logging") == "1"
        on_str = _("settings.on")
        off_str = _("settings.off")
        try:
            db_desc = f"{config.DB_PATH} ({config.DB_PATH.stat().st_size / 1024:,.0f} KB)"
        except OSError:
            db_desc = str(config.DB_PATH)

        lines = [
            _("settings.dev.version_label", version=_app_version()),
            _("settings.dev.debug_label", state=on_str if debug_on else off_str),
            _("settings.dev.logs_dir_label", path=_esc(str(debug_log.logs_dir()))),
            _("settings.dev.db_label", path=_esc(db_desc)),
            _("settings.dev.hevy_sync_label", status=_sync_status_str("last_sync_result")),
            _("settings.dev.fit_sync_label", status=_sync_status_str("fit_last_sync_result")),
        ]
        console.print(Panel("\n".join(lines), title=_("settings.dev.title"), border_style="cyan", padding=(0, 2)))

        action = questionary.select(
            _("settings.dev.prompt"),
            choices=[
                questionary.Choice(_("settings.dev.export_choice"), value="export"),
                questionary.Choice(_("settings.dev.import_choice"), value="import"),
                questionary.Choice(_("settings.dev.ai_context_choice"), value="ai_context"),
                questionary.Choice(_("settings.dev.db_info_choice"), value="db_info"),
                questionary.Choice(
                    _("settings.dev.debug_choice", state=on_str if debug_on else off_str), value="debug"
                ),
                questionary.Choice(_("settings.dev.clear_logs_choice"), value="clear_logs"),
                questionary.Separator("  ───"),
                questionary.Choice(_("settings.dev.reset_choice"), value="reset"),
                questionary.Separator("  ───"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "export":
            _do_export_data()

        elif action == "import":
            _do_import_data()

        elif action == "ai_context":
            # Builds the exact <training_data> block the chat sends to the AI
            # provider — entirely local, no API call.
            from ai.coach import _build_context

            slim = get_pref("ai_chat_slim") != "0"
            include_routines = get_pref("ai_include_routines") != "0"
            ctx = _build_context(8, slim=slim, include_routine=include_routines)
            out_dir = config.export_dir()
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"lifter-ai-context-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.md"
            path.write_text(ctx, encoding="utf-8")
            _dlog("EXPORT", "AI context preview written", chars=len(ctx))
            console.print(
                _(
                    "settings.dev.ai_context_done",
                    path=_esc(str(path)),
                    chars=f"{len(ctx):,}",
                    tokens=f"{len(ctx) // 4:,}",
                )
            )
            questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()

        elif action == "db_info":
            table_names = [
                r["name"]
                for r in query(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            info_lines = [
                _("settings.dev.db_info_row", table=t, count=query(f'SELECT COUNT(*) AS n FROM "{t}"')[0]["n"])
                for t in table_names
            ]
            console.print(
                Panel("\n".join(info_lines), title=_("settings.dev.db_info_title"), border_style="cyan", padding=(0, 2))
            )
            questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()

        elif action == "debug":
            new_val = not debug_on
            set_pref("debug_logging", "1" if new_val else "0")
            debug_log.enable(new_val)
            _dlog("SETTING", "debug_logging changed", value="on" if new_val else "off")
            if new_val:
                console.print(_("settings.dev.debug_enabled", logs_dir=debug_log.logs_dir()))
            else:
                console.print(_("settings.dev.debug_disabled"))
            questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()

        elif action == "clear_logs":
            log_files = sorted(debug_log.logs_dir().glob("debug-*.log"))
            if confirm_destructive(_("settings.dev.clear_logs_confirm", count=len(log_files))):
                for f in log_files:
                    f.unlink(missing_ok=True)
                _dlog("SETTING", "debug logs cleared", count=len(log_files))
                console.print(_("settings.dev.clear_logs_done", count=len(log_files)))
                questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()

        elif action == "reset":
            _do_data_reset()


def _do_about() -> None:
    from cli import _app_version

    console.clear()
    console.print(
        Panel(
            _("about.body", version=_app_version()),
            title=_("about.title"),
            border_style="cyan",
            padding=(1, 3),
        )
    )
    questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()


def _do_settings() -> None:
    while True:
        console.clear()
        action = questionary.select(
            _("settings.menu_prompt"),
            choices=[
                questionary.Choice(_("settings.profiles_choice"), value="profiles"),
                questionary.Choice(_("settings.profile_choice"), value="profile"),
                questionary.Choice(_("settings.prefs_choice"), value="prefs"),
                questionary.Choice(_("settings.ai_choice"), value="ai"),
                questionary.Choice(_("settings.dev_choice"), value="dev"),
                questionary.Separator("  ───────────────────────────────────────"),
                questionary.Choice(_("settings.about_choice"), value="about"),
                questionary.Separator("  ───────────────────────────────────────"),
                questionary.Choice(_("nav.back"), value="back"),
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
        elif action == "dev":
            _do_developer_settings()
        elif action == "about":
            _do_about()
