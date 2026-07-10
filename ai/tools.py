"""Tool-call handlers for the AI coach chat: routine push/update, goal
management, and exercise lookup — including the confirmation UI each one shows
before anything is persisted."""

import json

import questionary
from rich.console import Console
from rich.panel import Panel

from ai.prompts import _ai_lang_instruction
from ai.provider import stream_complete
from ai.sanitize import ANTI_INJECTION_PREAMBLE, sanitize_for_prompt
from db.goals import get_goals, get_pref
from db.store import get_routines_with_exercises, query
from i18n import _

console = Console()


def _stamp_routine(routine: dict) -> dict:
    """Return a copy of the routine with the Lifter watermark appended to notes (idempotent)."""
    stamped = dict(routine)
    existing = (stamped.get("notes") or "").strip()
    tag = "✦ Powered by Lifter"
    if tag in existing:
        return stamped
    stamped["notes"] = f"{existing}\n\n{tag}".strip() if existing else tag
    return stamped


def push_routine_to_hevy(routine_data: dict) -> dict:
    from hevy.client import HevyClient

    return HevyClient().create_routine(_stamp_routine(routine_data))


def _generate_benefits(exercises: list) -> dict:
    """Generate a {exercise title: benefits} map on demand.

    Benefits are no longer produced during routine generation (that wasted output
    tokens on every report, since benefits only show when a routine is pushed).
    This makes one small, focused call instead, tailored to the athlete's goals.
    """
    titles: list[str] = []
    for ex in exercises:
        if not isinstance(ex, dict):
            continue
        title = ex.get("title") or ex.get("exercise_template_id")
        if title and title not in titles:
            titles.append(title)
    if not titles:
        return {}

    goals = get_goals()
    goals_line = ""
    if goals:
        descs = ", ".join(sanitize_for_prompt(g["description"], max_len=80) for g in goals[:5])
        goals_line = f"The athlete's goals: {descs}.\n"

    lang = get_pref("ai_language") or "English"
    lang_line = f"\nRespond entirely in {_ai_lang_instruction(lang)}." if lang != "English" else ""
    system = (
        ANTI_INJECTION_PREAMBLE + "You are a strength and hypertrophy coach. For each exercise, write 2-3 sentences "
        "on its main benefits for the athlete's goals. "
        "Return ONLY a JSON object mapping each exercise title to its benefits string." + lang_line
    )
    prompt = goals_line + "Exercises:\n" + "\n".join(f"- {t}" for t in titles)

    try:
        raw = "".join(stream_complete(prompt, system=system, max_tokens=1024)).strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(raw)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def _show_exercise_benefits(exercises: list) -> None:
    """Display a benefits panel for each exercise, generating benefits on demand
    when they were not produced during routine generation (the common case now)."""
    missing = [
        ex
        for ex in exercises
        if isinstance(ex, dict)
        and not (ex.get("benefits") or "").strip()
        and (ex.get("title") or ex.get("exercise_template_id"))
    ]
    generated: dict = {}
    if missing:
        # Benefits are purely cosmetic — the routine is already saved. Let Ctrl+C skip
        # the generation gracefully instead of crashing the app (KeyboardInterrupt is a
        # BaseException, so _generate_benefits' own `except Exception` does not catch it).
        try:
            with console.status(_("coach.generating_benefits"), spinner="dots"):
                generated = _generate_benefits(missing)
        except KeyboardInterrupt:
            console.print(_("coach.benefits_skipped"))
            generated = {}

    benefit_lines = []
    for ex in exercises:
        if not isinstance(ex, dict):
            continue
        title = ex.get("title") or ex.get("exercise_template_id", "Exercise")
        benefits = (ex.get("benefits") or "").strip() or generated.get(title, "").strip()
        if not benefits:
            continue
        benefit_lines.append(f"[bold]{title}[/bold]")
        benefit_lines.append(f"  {benefits}")
        benefit_lines.append("")
    if benefit_lines:
        console.print(
            Panel(
                "\n".join(benefit_lines).strip(),
                title=_("chat.exercise_benefits_title"),
                border_style="green",
            )
        )


def _reject_invalid_routine(errors: list[str]) -> dict:
    """Tool result for garbage routine args — tells the model exactly what to fix."""
    import debug_log

    debug_log.error("AI", "Invalid routine tool args rejected", errors="; ".join(errors)[:300])
    console.print(_("chat.routine_invalid"))
    return {
        "success": False,
        "error": (
            "Invalid routine data: "
            + "; ".join(errors)[:500]
            + ". Regenerate the routine with valid fields (shorter notes if needed)."
        ),
    }


def _render_routine_panel(routine: dict, *, title: str, border_style: str, subtitle: str = "") -> None:
    """Shared preview panel for both routine creation and update."""
    header = f"[bold]{routine.get('title')}[/bold]"
    if subtitle:
        header += f"  [dim]{subtitle}[/dim]"
    lines = [header]
    if routine.get("notes"):
        lines.append(f"[dim]{routine['notes']}[/dim]")
    lines.append("")
    for ex in routine.get("exercises", []):
        if not isinstance(ex, dict):
            continue
        sets_desc = "  ".join(
            f"[dim]{s.get('type', 'normal')}[/dim] {s.get('weight_kg') or 'BW'}kg×{s.get('reps', '?')}"
            for s in ex.get("sets", [])
            if isinstance(s, dict)
        )
        note = f"\n    [dim italic]{ex['notes']}[/dim italic]" if ex.get("notes") else ""
        ex_title = ex.get("title") or ex.get("exercise_template_id", "Exercise")
        lines.append(f"  • [bold]{ex_title}[/bold]  {sets_desc}{note}")

    console.print(Panel("\n".join(lines), title=title, border_style=border_style))


def _warn_unknown_template_ids(routine: dict) -> None:
    invalid_ids = [
        ex.get("exercise_template_id", "")
        for ex in routine.get("exercises", [])
        if isinstance(ex, dict)
        and ex.get("exercise_template_id")
        and not query("SELECT 1 FROM exercise_templates WHERE id = ?", (ex["exercise_template_id"],))
    ]
    if invalid_ids:
        console.print(_("chat.routine_invalid_ids", count=len(invalid_ids), ids=", ".join(invalid_ids[:3])))


def _show_and_confirm_routine(routine: dict) -> dict:
    """Show the proposed routine, ask for confirmation, push if approved. Returns tool result."""
    from ai.routine_schema import validate_routine_args
    from hevy.client import HevyClient, _routine_id

    validated, errors = validate_routine_args(routine)
    if validated is None:
        return _reject_invalid_routine(errors)
    routine = validated

    _render_routine_panel(routine, title=_("chat.routine_panel_title"), border_style="cyan")
    _warn_unknown_template_ids(routine)

    if not questionary.confirm(_("chat.push_routine_prompt"), default=True).ask():
        from debug_log import log

        log("AI", "Routine push declined by user")
        console.print(_("chat.routine_not_pushed"))
        return {"success": False, "message": "User declined"}

    try:
        with console.status(_("chat.saving_routine"), spinner="dots"):
            from debug_log import log

            resp = HevyClient().create_routine(_stamp_routine(routine))
            routine_id = _routine_id(resp)
        log("AI", "Routine pushed to Hevy", routine_id=routine_id, exercises=len(routine.get("exercises", [])))
        console.print(_("chat.routine_pushed", routine_id=routine_id))
        _show_exercise_benefits(routine.get("exercises", []))
        return {"success": True, "routine_id": routine_id}
    except Exception as e:
        from debug_log import log

        log("ERROR", f"Routine push failed: {type(e).__name__}", error=str(e)[:200])
        console.print(f"[red]Failed: {e}[/red]\n")
        return {"success": False, "error": str(e)}


def _show_and_confirm_routine_update(fc_args: dict) -> dict:
    """Show the proposed routine update, ask for confirmation, push if approved."""
    from ai.routine_schema import validate_routine_args
    from db.store import upsert_routine
    from hevy.client import HevyClient

    validated, errors = validate_routine_args(fc_args, require_routine_id=True)
    if validated is None:
        return _reject_invalid_routine(errors)

    routine_id = validated["routine_id"]
    new_routine = {k: v for k, v in validated.items() if k != "routine_id"}

    # Look up current routine name from DB for reference
    current_routines = get_routines_with_exercises()
    current = next((r for r in current_routines if str(r["id"]) == routine_id), None)
    current_title = current["title"] if current else routine_id

    _render_routine_panel(
        new_routine,
        title=_("chat.routine_update_panel_title"),
        border_style="yellow",
        subtitle=f"(updating: {current_title})",
    )
    _warn_unknown_template_ids(new_routine)

    if not questionary.confirm(_("chat.save_changes_prompt"), default=True).ask():
        from debug_log import log

        log("AI", "Routine update declined by user", routine_id=routine_id)
        console.print(_("chat.update_cancelled"))
        return {"success": False, "message": "User declined"}

    try:
        with console.status(_("chat.updating_routine"), spinner="dots"):
            from debug_log import log

            HevyClient().update_routine(routine_id, _stamp_routine(new_routine))
            upsert_routine({"id": routine_id, **new_routine})
        log("AI", "Routine updated in Hevy", routine_id=routine_id)
        console.print(_("chat.routine_updated", routine_id=routine_id))
        _show_exercise_benefits(new_routine.get("exercises", []))
        return {"success": True, "routine_id": routine_id}
    except Exception as e:
        from debug_log import log

        log("ERROR", f"Routine update failed: {type(e).__name__}", routine_id=routine_id, error=str(e)[:200])
        console.print(f"[red]Failed: {e}[/red]\n")
        return {"success": False, "error": str(e)}


def _handle_find_exercises(fc_args: dict) -> dict:
    """Search the full exercise catalogue. Read-only, returns matching templates."""
    where: list[str] = []
    params: list = []
    q = (fc_args.get("query") or "").strip()
    if q:
        where.append("title LIKE ?")
        params.append(f"%{q}%")
    mg = (fc_args.get("muscle_group") or "").strip()
    if mg:
        where.append("primary_muscle_group LIKE ?")
        params.append(f"%{mg}%")
    ty = (fc_args.get("type") or "").strip()
    if ty:
        where.append("type LIKE ?")
        params.append(f"%{ty}%")

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = query(
        f"""SELECT id, title, type, primary_muscle_group
            FROM exercise_templates{clause}
            ORDER BY primary_muscle_group, title
            LIMIT 50""",
        tuple(params),
    )
    matches = [
        {
            "exercise_template_id": r["id"],
            "title": r["title"],
            "type": r["type"],
            "muscle_group": r["primary_muscle_group"],
        }
        for r in rows
    ]
    from debug_log import log

    log("AI", "find_exercises", query=q, muscle_group=mg, type=ty, matches=len(matches))
    return {
        "count": len(matches),
        "exercises": matches,
        "note": "Truncated to 50 results — refine the search if needed." if len(matches) == 50 else None,
    }


def _handle_manage_goals(fc_args: dict) -> dict:
    """Handle a goal add/update/remove request. Returns tool result."""
    from db.goals import delete_goal, get_goals, save_goal, update_goal_fields

    action = fc_args.get("action")
    summary = fc_args.get("changes_summary", "Modify a goal")

    if action in ("update", "remove"):
        gid = fc_args.get("goal_id")
        valid_ids = {g["id"] for g in get_goals()}
        if gid is None or int(gid) not in valid_ids:
            console.print(_("chat.goal_invalid_id", gid=gid))
            return {"success": False, "error": f"Goal ID {gid} does not exist"}

    console.print(
        Panel(
            f"[bold]{sanitize_for_prompt(summary, max_len=200)}[/bold]",
            title=_("chat.goal_panel_title"),
            border_style="yellow",
        )
    )

    if not questionary.confirm(_("chat.apply_change_prompt"), default=True).ask():
        from debug_log import log

        log("AI", "Goal change declined by user", action=action)
        console.print(_("chat.change_not_applied"))
        return {"success": False, "message": "User declined"}

    try:
        with console.status(_("chat.applying_change"), spinner="dots"):
            if action == "add":
                goal_type = fc_args.get("goal_type", "custom")
                # Capture the current body metric as the baseline so progress tracks from
                # today (mirrors the CLI wizard). Without this, start_value stays NULL and
                # progress is stuck at 0%.
                start_value = None
                if goal_type in ("weight_loss", "weight_gain"):
                    _rows = query(
                        "SELECT weight_kg FROM body_measurements WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT 1"
                    )
                    start_value = float(_rows[0]["weight_kg"]) if _rows else None
                elif goal_type == "body_fat":
                    _rows = query(
                        "SELECT fat_percent FROM body_measurements WHERE fat_percent IS NOT NULL ORDER BY date DESC LIMIT 1"
                    )
                    start_value = float(_rows[0]["fat_percent"]) if _rows else None
                save_goal(
                    type=goal_type,
                    description=fc_args.get("description", ""),
                    target=fc_args.get("target"),
                    unit=fc_args.get("unit"),
                    exercise_template_id=fc_args.get("exercise_template_id"),
                    exercise_name=fc_args.get("exercise_name"),
                    muscle_group=fc_args.get("muscle_group"),
                    start_value=start_value,
                )
                label = _("chat.goal_added")
                result: dict = {"success": True, "action": "added"}

            elif action == "update":
                gid = fc_args.get("goal_id")
                if not gid:
                    raise ValueError("goal_id is required for update")
                update_goal_fields(
                    goal_id=int(gid),
                    description=fc_args.get("description"),
                    target=fc_args.get("target"),
                    unit=fc_args.get("unit"),
                )
                label = _("chat.goal_updated")
                result = {"success": True, "action": "updated"}

            elif action == "remove":
                gid = fc_args.get("goal_id")
                if not gid:
                    raise ValueError("goal_id is required for remove")
                delete_goal(int(gid))
                label = _("chat.goal_removed")
                result = {"success": True, "action": "removed"}

            else:
                raise ValueError(f"Unknown action: {action}")

        from debug_log import log

        log("AI", "Goal change applied", action=action)
        console.print(f"{label}\n")
        return result

    except Exception as e:
        from debug_log import log

        log("ERROR", f"Goal change failed: {type(e).__name__}", action=action, error=str(e)[:200])
        console.print(f"[red]Failed: {e}[/red]\n")
        return {"success": False, "error": str(e)}
