"""Interactive coach chat: readline loop, tool-call dispatch, weak-model
nudges, and end-of-session memory extraction."""

import contextlib
import re
import readline

from rich.console import Console
from rich.markdown import Markdown

import paths
from ai.context import _build_context
from ai.errors import _friendly_error
from ai.memory import _extract_and_save_memories, _tool_action_log_entry
from ai.prompts import (
    _CHAT_SYSTEM_BASE,
    _FIND_EXERCISES_TOOL,
    _MANAGE_GOALS_TOOL,
    _PUSH_ROUTINE_TOOL,
    _UPDATE_ROUTINE_TOOL,
    _ai_lang_instruction,
)
from ai.provider import create_chat_session, provider_label
from ai.tools import (
    _handle_find_exercises,
    _handle_manage_goals,
    _show_and_confirm_routine,
    _show_and_confirm_routine_update,
)
from db.goals import get_pref
from i18n import _

console = Console()

_CHAT_HISTORY_FILE = paths.CHAT_HISTORY_FILE

_ANSI_RE = re.compile(r"(\x1b\[[0-9;]*m)")

# Routine tool calls carry per-exercise notes and easily exceed the 4096-token
# default. A few OpenAI-compat models cap completions below 8192 and return a
# 400 (surfaced by _friendly_error) — lower this if that bites.
_CHAT_MAX_TOKENS = 8192


def _readline_prompt(markup: str) -> str:
    """Render rich markup into a readline-safe input prompt.

    The prompt must be passed to input() (not printed separately) so readline
    knows the cursor offset, and any ANSI escapes must be wrapped in \\001/\\002
    so readline counts only the visible width. Without this, wrapping a long line
    overwrites earlier text and backspace can delete into the prompt itself.
    """
    with console.capture() as cap:
        console.print(markup, end="")
    return _ANSI_RE.sub(lambda m: "\001" + m.group(1) + "\002", cap.get())


# ── weak-model tool-call nudge ────────────────────────────────────────────────

_ROUTINE_SIGNALS = [
    "sets",
    "reps",
    "treino",
    "workout",
    "routine",
    "exercício",
    "exercise",
    "warmup",
    "normal",
    "dropset",
    "kg×",
    "kg x",
    "agachamento",
    "supino",
    "remada",
    "rosca",
    "tríceps",
    "desenvolvimento",
    "levantamento",
]
_GOAL_SIGNALS = ["goal", "meta", "objetivo", "target", "alvo", "added", "removed", "updated"]


def _missed_tool_call_nudge(text: str) -> str | None:
    """Return a nudge prompt when the model described a tool action in plain text instead of calling it."""
    lower = text.lower()
    routine_hits = sum(1 for s in _ROUTINE_SIGNALS if s in lower)
    goal_hits = sum(1 for s in _GOAL_SIGNALS if s in lower)

    if routine_hits >= 4:
        return (
            "[INSTRUCTION] You described a workout routine in plain text but did not call "
            "push_routine. You MUST call the push_routine tool now with the routine data. "
            "Do not write any more plain-text descriptions — call the tool immediately."
        )
    if goal_hits >= 2:
        return (
            "[INSTRUCTION] You described a goal change in plain text but did not call "
            "manage_goals. You MUST call the manage_goals tool now with the change details. "
            "Call the tool immediately."
        )
    return None


# ── enhanced chat ─────────────────────────────────────────────────────────────


def start_enhanced_chat(weeks: int = 8) -> None:
    """Interactive chat with tool calling, goal management, and memory persistence."""
    import config as _cfg
    from debug_log import log as _log

    slim = get_pref("ai_chat_slim") != "0"  # default True unless explicitly disabled
    # The athlete's saved routines are included by default (helps the coach create,
    # edit, and analyse routines); configurable via Settings → AI. Creating a routine
    # still requires an explicit request — the push_routine tool only fires when asked.
    include_routines = get_pref("ai_include_routines") != "0"
    context = _build_context(weeks, slim=slim, include_routine=include_routines)
    _log(
        "AI",
        "Chat session started",
        provider=_cfg.AI_PROVIDER,
        model=_cfg.AI_MODEL,
        weeks=weeks,
        slim=slim,
        lang=get_pref("ai_language") or "English",
    )
    lang = get_pref("ai_language") or "English"
    lang_line = f"\nAlways respond entirely in {_ai_lang_instruction(lang)}.\n" if lang != "English" else ""
    # Use XML-like delimiters so the model can clearly distinguish
    # instructions (above) from untrusted data (below).
    system = f"{_CHAT_SYSTEM_BASE}{lang_line}\n\n<training_data>\n{context}\n</training_data>"

    session = create_chat_session(
        system=system,
        tools=[_PUSH_ROUTINE_TOOL, _UPDATE_ROUTINE_TOOL, _MANAGE_GOALS_TOOL, _FIND_EXERCISES_TOOL],
        max_tokens=_CHAT_MAX_TOKENS,
    )

    console.rule(_("chat.rule_title"))
    console.print(_("chat.hint", provider=provider_label(), weeks=weeks))

    try:
        from db.goals import token_budget_status

        budget_status = token_budget_status()
        if budget_status and budget_status["pct"] >= 100:
            console.print(
                _("chat.budget_exceeded", used=f"{budget_status['used']:,}", budget=f"{budget_status['budget']:,}")
            )
        elif budget_status and budget_status["pct"] >= 80:
            console.print(
                _(
                    "chat.budget_warning",
                    pct=int(budget_status["pct"]),
                    used=f"{budget_status['used']:,}",
                    budget=f"{budget_status['budget']:,}",
                )
            )
    except Exception as e:
        from debug_log import error as _err

        _err("AI", "budget banner skipped", exc=e)

    try:
        readline.read_history_file(_CHAT_HISTORY_FILE)
        readline.set_history_length(200)
    except OSError:
        pass

    conversation_log: list[dict] = []

    while True:
        try:
            user_input = input(_readline_prompt(_("chat.you_prompt"))).strip()
        except (EOFError, KeyboardInterrupt):
            console.print(_("chat.returning_to_menu"))
            break

        if not user_input:
            continue
        quit_words = {w.strip().lower() for w in _("chat.quit_words").split(",") if w.strip()}
        if user_input.lower() in ("quit", "exit", "q", "bye") or user_input.lower() in quit_words:
            break

        conversation_log.append({"role": "user", "content": user_input})
        console.print()

        # ── main call ────────────────────────────────────────────────────────
        try:
            with console.status(_("chat.thinking"), spinner="dots"):
                response = session.send(user_input)
        except KeyboardInterrupt:
            session.discard_pending_user()
            console.print(_("chat.cancelled"))
            continue
        except Exception as e:
            console.print(f"[red]{_friendly_error(e)}[/red]\n")
            continue

        if response.text:
            console.print(_("chat.coach_label"))
            console.print(Markdown(response.text))
            console.print()
            conversation_log.append({"role": "assistant", "content": response.text})
        if response.stop_reason == "max_tokens" and not response.tool_calls:
            console.print(_("chat.response_truncated"))

        # ── weak-model nudge ─────────────────────────────────────────────────
        if response.text and not response.tool_calls:
            nudge = _missed_tool_call_nudge(response.text)
            if nudge:
                try:
                    with console.status(_("chat.thinking_short"), spinner="dots"):
                        response = session.send(nudge)
                except KeyboardInterrupt:
                    session.discard_pending_user()
                    console.print(_("chat.cancelled"))
                    continue
                except Exception:
                    continue

        # ── tool call handling ───────────────────────────────────────────────
        # Loop so the model can chain tools — e.g. find_exercises to look up an id,
        # then push_routine with it. A small cap guards against a runaway loop.
        _tool_rounds = 0
        while response.tool_calls and _tool_rounds < 8:
            _tool_rounds += 1
            tool_results: list[tuple] = []
            if response.stop_reason == "max_tokens":
                # Truncated tool arguments are unusable (JSON fragments leak into
                # field values) — never dispatch them; ask the model to retry.
                _log("AI", "Tool call truncated at max_tokens", tools=",".join(tc.name for tc in response.tool_calls))
                console.print(_("chat.response_truncated"))
                tool_results = [
                    (
                        tc,
                        {
                            "success": False,
                            "error": "Your response was cut off at the token limit, so the tool "
                            "arguments were incomplete. Retry with more concise exercise notes.",
                        },
                    )
                    for tc in response.tool_calls
                ]
            else:
                for tc in response.tool_calls:
                    _log("AI", f"Tool call: {tc.name}")
                    if tc.name == "push_routine":
                        result = _show_and_confirm_routine(dict(tc.args))
                    elif tc.name == "update_routine":
                        result = _show_and_confirm_routine_update(dict(tc.args))
                    elif tc.name == "manage_goals":
                        result = _handle_manage_goals(dict(tc.args))
                    elif tc.name == "find_exercises":
                        result = _handle_find_exercises(dict(tc.args))
                    else:
                        result = {"error": f"Unknown tool: {tc.name}"}
                    tool_results.append((tc, result))

                    # Durable decisions (routines, goals — including declines) feed
                    # the end-of-chat memory extraction.
                    entry = _tool_action_log_entry(tc.name, dict(tc.args), result)
                    if entry:
                        conversation_log.append({"role": "assistant", "content": entry})

            try:
                with console.status(_("chat.thinking_short"), spinner="dots"):
                    response = session.submit_tool_results(tool_results)
            except KeyboardInterrupt:
                console.print(_("chat.cancelled"))
                break
            except Exception as e:
                console.print(f"[red]{_friendly_error(e)}[/red]\n")
                break

            if response.text:
                console.print(_("chat.coach_label"))
                console.print(Markdown(response.text))
                console.print()
                conversation_log.append({"role": "assistant", "content": response.text})
            if response.stop_reason == "max_tokens" and not response.tool_calls:
                console.print(_("chat.response_truncated"))

    with contextlib.suppress(OSError):
        readline.write_history_file(_CHAT_HISTORY_FILE)

    # ── log session totals ────────────────────────────────────────────────────
    _log("AI", "Chat session ended", turns=len([m for m in conversation_log if m["role"] == "user"]))

    # ── extract and save memories after session ends ──
    if len(conversation_log) >= 2:
        with console.status(_("chat.saving_insights"), spinner="dots"):
            saved = _extract_and_save_memories(conversation_log)
        _log("AI", "Memories extracted", saved=saved)
        if saved > 0:
            console.print(_("chat.insights_saved", count=saved))
        else:
            console.print(_("chat.no_insights"))
