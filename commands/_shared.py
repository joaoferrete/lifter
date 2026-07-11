"""Helpers shared across command modules — imports nothing from cli.

cli.py re-exports these names, so existing `cli.NO_PAUSE` / `cli._dlog`
references (main loop, tests) keep working.
"""

from typing import Any

# Menu actions that manage their own screen pacing return this sentinel;
# any other return value means the main loop should _pause() so output
# (e.g. a guard-failure error) stays visible before the next console.clear().
NO_PAUSE: object = object()

# UI languages (code, label) offered at profile creation and in Preferences.
_UI_LANGUAGES: list[tuple[str, str]] = [
    ("en", "English"),
    ("pt_BR", "Português (Brasil)"),
]


def _dlog(category: str, msg: str, **kv: Any) -> None:
    """Forward to debug_log.log without ever raising."""
    try:
        import debug_log

        debug_log.log(category, msg, **kv)
    except Exception:
        pass
