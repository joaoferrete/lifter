"""questionary wrappers: consistent styling, localized defaults, and a single
standard for destructive-action confirmation."""

from collections.abc import Callable

import questionary

from i18n import _
from ui.console import STYLE, console


def pause() -> None:
    """Localized 'press any key' — never the library's English default."""
    console.print()
    questionary.press_any_key_to_continue(_("nav.press_any_key")).ask()


def is_number(v: str) -> bool:
    """True for a plain positive decimal number ('82', '82.5') — at most one dot.

    The old inline validators used v.replace('.', '').isdigit(), which accepted
    '1.2.3' and then crashed on float().
    """
    return v.replace(".", "", 1).isdigit()


def number_validator(error_message: str) -> "Callable[[str], bool | str]":
    """questionary validate= callable accepting only plain decimal numbers."""

    def _validate(v: str) -> bool | str:
        return True if is_number(v.strip()) else error_message

    return _validate


def confirm_destructive(message: str, *, double: bool = False) -> bool:
    """Standard confirmation for destructive actions (default No).

    double=True asks twice — reserved for irreversible bulk deletions
    (full data wipe, import overwrite)."""
    first = questionary.confirm(message, default=False, style=STYLE).ask()
    if not first:
        return False
    if double:
        return bool(questionary.confirm(_("confirm.really_sure"), default=False, style=STYLE).ask())
    return True


def week_choices(weeks: list[int]) -> list:
    """Localized '{n} weeks' choices whose values stay the canonical 'N weeks'
    string (parsed elsewhere via parse_weeks_value and stored as a pref)."""
    return [questionary.Choice(_("time.weeks", n=n), value=f"{n} weeks") for n in weeks]


def day_choices(days: list[int]) -> list:
    return [questionary.Choice(_("time.days", n=n), value=f"{n} days") for n in days]
