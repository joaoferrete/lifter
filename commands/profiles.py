"""Profiles — creation flow, management menu, per-profile settings, and startup bootstrap."""

import contextlib

import questionary
from rich.markup import escape as _esc
from rich.panel import Panel

import config
from commands._shared import _UI_LANGUAGES, _dlog
from commands.body import _onboard_body_metrics, _prompt_and_save_height
from db.goals import get_height_cm, get_pref, set_pref
from db.store import init_db
from i18n import _
from ui.console import STYLE, console
from ui.format import fmt_height as _fmt_height


def _do_create_profile_flow() -> str:
    """Interactive profile creation. Returns the new slug."""
    from profile_mgr import PROFILES_DIR, create_profile

    name = (
        questionary.text(
            _("profiles.name_prompt"),
            validate=lambda v: bool(v.strip()) or _("validate.name_required"),
            style=STYLE,
        ).ask()
        or ""
    ).strip()
    if not name:
        name = "New Profile"
    api_key = (
        questionary.text(
            _("profiles.api_key_prompt"),
            style=STYLE,
        ).ask()
        or ""
    ).strip()
    lang_choices = [questionary.Choice(lname, value=code) for code, lname in _UI_LANGUAGES]
    lang_code = (
        questionary.select(
            _("profiles.language_prompt"),
            choices=lang_choices,
            style=STYLE,
        ).ask()
        or "en"
    )
    profile = create_profile(name, hevy_api_key=api_key)
    slug = profile["slug"]
    # Write the language pref into the new profile's DB now so it survives
    # a process restart when the user switches to this profile immediately.
    old_db = config.DB_PATH
    config.DB_PATH = PROFILES_DIR / slug / "hevy.db"
    try:
        init_db()
        set_pref("ui_language", lang_code)
    finally:
        config.DB_PATH = old_db
    console.print(_("profiles.created", name=_esc(name)))
    return slug


def _do_profiles_menu() -> None:
    from profile_mgr import (
        delete_profile,
        get_active_slug,
        get_profile_name,
        list_profiles,
        rename_profile,
        set_active_slug,
    )

    while True:
        console.clear()
        active_slug = get_active_slug()
        active_name = get_profile_name(active_slug) if active_slug else "None"
        profiles = list_profiles()

        console.print(
            Panel(
                _("profiles.panel_content", name=_esc(active_name), total=len(profiles)),
                title=_("profiles.panel_title"),
                border_style="cyan",
                padding=(0, 2),
            )
        )

        action = questionary.select(
            _("profiles.menu_prompt"),
            choices=[
                questionary.Choice(_("profiles.switch_choice"), value="switch"),
                questionary.Choice(_("profiles.create_choice"), value="create"),
                questionary.Choice(_("profiles.rename_choice"), value="rename"),
                questionary.Choice(_("profiles.delete_choice"), value="delete"),
                questionary.Separator("  ───────────────────────────────────────"),
                questionary.Choice(_("nav.back"), value="back"),
            ],
            style=STYLE,
        ).ask()

        if not action or action == "back":
            return

        if action == "switch":
            if len(profiles) <= 1:
                console.print(_("profiles.only_one"))
                questionary.press_any_key_to_continue(_("nav.press_any_key"), style=STYLE).ask()
                continue
            choices = [
                questionary.Choice(
                    f"  {p['name']}{_('profiles.active_suffix') if p['slug'] == active_slug else ''}",
                    value=p["slug"],
                )
                for p in profiles
            ]
            choices.append(questionary.Separator("  ──────────────────────────────────"))
            choices.append(questionary.Choice(_("nav.cancel"), value=None))
            slug = questionary.select(_("profiles.switch_prompt"), choices=choices, style=STYLE).ask()
            if slug and slug != active_slug:
                _dlog("PROFILE", "Profile switch requested", from_slug=active_slug, to_slug=slug)
                set_active_slug(slug)
                console.print(_("profiles.switching", name=_esc(get_profile_name(slug))))
                import os as _os
                import sys as _sys

                _os.execv(_sys.executable, [_sys.executable, *_sys.argv])

        elif action == "create":
            slug = _do_create_profile_flow()
            if questionary.confirm(_("profiles.switch_now"), default=True, style=STYLE).ask():
                _dlog("PROFILE", "Switched to newly created profile", slug=slug)
                set_active_slug(slug)
                import os as _os
                import sys as _sys

                _os.execv(_sys.executable, [_sys.executable, *_sys.argv])

        elif action == "rename":
            if active_slug:
                new_name = (
                    questionary.text(
                        _("profiles.rename_prompt", name=_esc(active_name)),
                        style=STYLE,
                    ).ask()
                    or ""
                ).strip()
                if new_name:
                    rename_profile(active_slug, new_name)
                    console.print(_("profiles.renamed", name=_esc(new_name)))

        elif action == "delete":
            others = [p for p in profiles if p["slug"] != active_slug]
            if not others:
                console.print(_("profiles.cannot_delete_only"))
                questionary.press_any_key_to_continue(_("nav.press_any_key"), style=STYLE).ask()
                continue
            choices = [questionary.Choice(f"  {p['name']}", value=p["slug"]) for p in others]
            choices.append(questionary.Separator("  ──────────────────────────────────"))
            choices.append(questionary.Choice(_("nav.cancel"), value=None))
            slug = questionary.select(_("profiles.delete_prompt"), choices=choices, style=STYLE).ask()
            if slug:
                pname = get_profile_name(slug)
                if questionary.confirm(
                    _("profiles.delete_confirm", name=_esc(pname)),
                    default=False,
                    style=STYLE,
                ).ask():
                    delete_profile(slug)
                    console.print(_("profiles.deleted", name=_esc(pname)))


def _do_profile_settings() -> None:
    import json as _json

    from profile_mgr import PROFILES_DIR, get_active_slug, update_profile_key

    active_slug = get_active_slug()
    name = get_pref("display_name") or ""

    hevy_key = ""
    if active_slug:
        cfg_file = PROFILES_DIR / active_slug / "profile.json"
        if cfg_file.exists():
            with contextlib.suppress(Exception):
                hevy_key = _json.loads(cfg_file.read_text()).get("hevy_api_key", "")
    masked_key = (hevy_key[:4] + "…" + hevy_key[-4:]) if len(hevy_key) > 8 else ("set" if hevy_key else "not set")

    height_cm = get_height_cm()
    height_line = _("profile.height_label", height=_fmt_height(height_cm)) if height_cm else _("profile.height_notset")

    name_line = _("profile.display_name_label", name=_esc(name)) if name else _("profile.display_name_notset")
    console.print(
        Panel(
            f"{name_line}\n{_('profile.api_key_label', key=masked_key)}\n{height_line}",
            title=_("profile.panel_title"),
            border_style="cyan",
            padding=(0, 2),
        )
    )

    action = questionary.select(
        _("profile.edit_prompt"),
        choices=[
            questionary.Choice(_("profile.display_name_choice"), value="name"),
            questionary.Choice(_("profile.api_key_choice"), value="apikey"),
            questionary.Choice(_("profile.height_choice"), value="height"),
            questionary.Choice(_("nav.cancel"), value="back"),
        ],
        style=STYLE,
    ).ask()

    if action == "name":
        new_name = (questionary.text(_("profile.new_name_prompt"), style=STYLE).ask() or "").strip()
        if new_name:
            set_pref("display_name", new_name)
            _dlog("SETTING", "display_name changed")
            console.print(_("profile.name_updated", name=_esc(new_name)))

    elif action == "apikey" and active_slug:
        new_key = (questionary.text(_("profile.new_api_key_prompt"), style=STYLE).ask() or "").strip()
        if new_key:
            update_profile_key(active_slug, new_key)
            config.HEVY_API_KEY = new_key
            _dlog("SETTING", "hevy_api_key updated", profile=active_slug)
            console.print(_("profile.api_key_updated"))

    elif action == "height":
        _prompt_and_save_height()


def _bootstrap_profiles() -> None:
    """Select or create a profile before any DB operations."""
    import shutil as _shutil
    from pathlib import Path as _Path

    import cli  # the legacy single-user hevy.db lived next to cli.py
    from profile_mgr import (
        PROFILES_DIR,
        PROFILES_FILE,
        activate_profile,
        create_profile,
        get_active_slug,
        list_profiles,
        set_active_slug,
    )

    project_dir = _Path(cli.__file__).resolve().parent
    old_db = project_dir / "hevy.db"
    old_token = project_dir / "fit_token.json"

    # Migration: existing single-user hevy.db with no profiles.json yet
    if not PROFILES_FILE.exists() and old_db.exists():
        console.print()
        console.print(
            Panel(
                _("migration.panel_body"),
                title=_("migration.panel_title"),
                border_style="cyan",
                padding=(0, 2),
            )
        )
        name = (
            questionary.text(
                _("migration.name_prompt"),
                default="Default",
                style=STYLE,
            ).ask()
            or "Default"
        ).strip()

        profile = create_profile(name, hevy_api_key=config.HEVY_API_KEY)
        slug = profile["slug"]
        profile_dir = PROFILES_DIR / slug
        _shutil.move(str(old_db), profile_dir / "hevy.db")
        if old_token.exists():
            _shutil.move(str(old_token), profile_dir / "fit_token.json")
        set_active_slug(slug)
        activate_profile(slug)
        _dlog("PROFILE", "Single-user data migrated to profile", slug=slug)
        console.print(_("migration.done", name=_esc(name)))
        console.print()
        return

    profiles = list_profiles()

    if not profiles:
        # First run — prompt for name, Hevy API key, and language
        console.print()
        console.rule(_("welcome.rule"))
        name = (
            questionary.text(
                _("welcome.name_prompt"),
                default="Athlete",
                style=STYLE,
            ).ask()
            or "Athlete"
        ).strip()
        api_key = (
            questionary.text(
                _("welcome.api_key_prompt"),
                style=STYLE,
            ).ask()
            or ""
        ).strip()
        lang_choices = [questionary.Choice(lname, value=code) for code, lname in _UI_LANGUAGES]
        lang_code = (
            questionary.select(
                _("welcome.language_prompt"),
                choices=lang_choices,
                style=STYLE,
            ).ask()
            or "en"
        )
        profile = create_profile(name, hevy_api_key=api_key)
        activate_profile(profile["slug"])
        init_db()
        set_pref("ui_language", lang_code)
        import i18n as _i18n

        _i18n.init(lang_code)
        _dlog("PROFILE", "First run: profile created", slug=profile["slug"])
        # Baseline body metrics — the user may not have Google Fit connected yet.
        _onboard_body_metrics()
        console.print()
        return

    if len(profiles) == 1:
        activate_profile(profiles[0]["slug"])
        return

    # Multiple profiles — show selector
    last = get_active_slug()
    choices = []
    for p in profiles:
        suffix = _("profiles.last_used_suffix") if p["slug"] == last else ""
        choices.append(questionary.Choice(f"  {p['name']}{suffix}", value=p["slug"]))
    choices.append(questionary.Separator("  ──────────────────────────────────"))
    choices.append(questionary.Choice(_("profiles.new_profile_choice"), value="_new"))

    console.clear()
    slug = questionary.select(_("profiles.select_prompt"), choices=choices, style=STYLE).ask()

    if not slug:
        slug = last or profiles[0]["slug"]
    elif slug == "_new":
        slug = _do_create_profile_flow()

    _dlog("PROFILE", "Profile selected at startup", slug=slug, total_profiles=len(profiles))
    set_active_slug(slug)
    activate_profile(slug)
