"""AI coaching facade — the one-shot coaching report plus the public surface
of the AI package.

The implementation lives in focused modules:
  ai/context.py  — prompt-context assembly from stored data
  ai/prompts.py  — prompt copy and tool schemas
  ai/tools.py    — tool-call handlers and their confirmation UI
  ai/memory.py   — end-of-chat memory extraction
  ai/chat.py     — the interactive chat loop
  ai/errors.py   — provider-exception → friendly message mapping
"""

from rich.console import Console

from ai.chat import start_enhanced_chat
from ai.context import _build_context
from ai.errors import _friendly_error, friendly_error
from ai.prompts import _COACH_SYSTEM, _ai_lang_instruction
from ai.provider import complete_json, provider_label
from ai.tools import _show_exercise_benefits, _stamp_routine, push_routine_to_hevy
from db.goals import get_pref
from i18n import _

__all__ = [
    "_build_context",
    "_friendly_error",
    "_show_exercise_benefits",
    "_stamp_routine",
    "friendly_error",
    "get_coaching",
    "push_routine_to_hevy",
    "start_enhanced_chat",
]

console = Console()


def get_coaching(weeks: int = 8, generate_routine: bool = False) -> dict:
    """One-shot coaching report: JSON scores, analysis, and optionally a routine."""
    import config as _cfg
    from db.goals import get_token_usage as _get_usage
    from debug_log import log

    _tokens_before = _get_usage()
    # The athlete's existing routines stay in the context by default — they help the
    # analysis and any routine edits — and this is configurable. Generating a NEW
    # routine, on the other hand, is always an explicit request.
    include_routines_ctx = (get_pref("ai_include_routines") != "0") or generate_routine
    log(
        "AI",
        "Coaching report started",
        provider=_cfg.AI_PROVIDER,
        model=_cfg.AI_MODEL,
        weeks=weeks,
        generate_routine=generate_routine,
        routines_in_context=include_routines_ctx,
    )
    # The one-shot report cannot use tools, so when it must generate a routine it needs
    # the full catalogue inline; plain analysis keeps the lean (previously-used) list.
    context = _build_context(weeks, include_routine=include_routines_ctx, full_library=generate_routine)
    lang = get_pref("ai_language") or "English"
    lang_line = f"\nAlways respond entirely in {_ai_lang_instruction(lang)}.\n" if lang != "English" else ""
    ask_line = (
        "Please analyse my training and generate a suggested next routine."
        if generate_routine
        else "Please analyse my training. Do NOT generate a routine."
    )
    # The training data goes in the (cacheable) system block rather than the user
    # message, so repeated reports within the cache TTL reuse it as a cache hit.
    routine_rule = (
        ""
        if generate_routine
        else (
            '\nIMPORTANT: The athlete did not request a routine. Omit the "routine" field '
            "entirely (set it to null) and do not generate any exercises.\n"
        )
    )
    system = f"{_COACH_SYSTEM}{lang_line}{routine_rule}\n\n<training_data>\n{context}\n</training_data>"
    prompt = ask_line

    console.print(_("coach.powered_by", provider=provider_label()))

    # A routine adds many exercises to the JSON; plain analysis is far smaller.
    report_max_tokens = 4096 if generate_routine else 2048
    with console.status(_("coach.generating"), spinner="dots"):
        result = complete_json(prompt, system=system, max_tokens=report_max_tokens)

    _tokens_after = _get_usage()
    log(
        "AI",
        "Coaching report complete",
        input=_tokens_after["input"] - _tokens_before["input"],
        output=_tokens_after["output"] - _tokens_before["output"],
        cache_read=_tokens_after["cache_read"] - _tokens_before["cache_read"],
    )
    return result
