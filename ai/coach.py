"""AI coaching — analyzes workout data, generates suggestions, manages goals."""
import json
import re
import readline
from pathlib import Path

import questionary
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from datetime import datetime, timezone

from ai.provider import create_chat_session, stream_complete, complete_json, provider_label, ToolCall
from ai.sanitize import sanitize_for_prompt, ANTI_INJECTION_PREAMBLE
from analytics.volume import muscle_group_summary, sets_per_muscle_per_week
from analytics.progression import detect_plateaus, top_progressions
from analytics.frequency import workout_frequency, muscle_group_frequency
from analytics.records import all_time_records, recent_prs, body_measurement_trend
from db.store import query, get_routines_with_exercises
from db.goals import goals_context_for_ai, get_pref, get_goals
from i18n import _

console = Console()

_CHAT_HISTORY_FILE = Path.home() / ".hevy_chat_history"

_ANSI_RE = re.compile(r"(\x1b\[[0-9;]*m)")


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


def _friendly_error(e: Exception) -> str:
    """Return a user-friendly error message for AI provider exceptions."""
    try:
        from debug_log import log
        import config as _cfg
        import traceback as _tb
        _status = getattr(e, "status_code", None) or getattr(e, "code", None)
        log("ERROR", f"{type(e).__name__}: {str(e)[:200]}",
            provider=_cfg.AI_PROVIDER, model=_cfg.AI_MODEL,
            status=_status, traceback=_tb.format_exc().splitlines()[-1])
    except Exception:
        pass

    msg = str(e)
    status = getattr(e, "status_code", None) or getattr(e, "code", None)

    # Try to extract status from common exception attribute patterns
    if status is None:
        for attr in ("response", "_response"):
            resp = getattr(e, attr, None)
            if resp is not None:
                status = getattr(resp, "status_code", None)
                break

    if status == 429:
        retry_after = None
        for attr in ("response", "_response"):
            resp = getattr(e, attr, None)
            if resp is not None:
                headers = getattr(resp, "headers", {}) or {}
                retry_after = headers.get("Retry-After") or headers.get("retry-after")
                break
        if not retry_after and "retry after" in msg.lower():
            import re
            m = re.search(r"(\d+)\s*s", msg, re.IGNORECASE)
            retry_after = m.group(1) if m else None
        if retry_after:
            return _("error.rate_limit_429", retry_after=retry_after)
        return _("error.rate_limit_429_no_retry")
    if status == 413 or "rate_limit_exceeded" in msg or "tokens per minute" in msg.lower():
        return _("error.request_too_large_413")
    if status == 401:
        return _("error.api_key_invalid_401")
    if status == 403:
        return _("error.access_denied_403")
    if status == 400:
        return _("error.bad_request_400")
    if status is not None and status >= 500:
        return _("error.server_error_5xx", status=status)
    if status is not None:
        return _("error.generic_status", status=status)
    return _("error.generic", exc_type=type(e).__name__)

_AI_LANG_MAP = {
    "Portuguese (BR)": "Brazilian Portuguese",
    "Portuguese (PT)": "European Portuguese",
}


def _ai_lang_instruction(lang: str) -> str:
    return _AI_LANG_MAP.get(lang, lang)


# ── context builder ───────────────────────────────────────────────────────────

def _build_context(weeks: int = 8, slim: bool = False, include_routine: bool = True) -> str:
    name = get_pref("display_name") or "the athlete"
    freq = workout_frequency(weeks)
    muscle_vol = muscle_group_summary(weeks)
    muscle_freq = muscle_group_frequency(weeks)
    sets_per_week = sets_per_muscle_per_week(weeks)
    plateaus = detect_plateaus(weeks)
    top_gains = top_progressions(weeks)
    prs = recent_prs(30)
    body = body_measurement_trend(weeks)

    # Exercise library and saved routines only matter when the model must build or
    # reference a routine — skip them otherwise to save input tokens.
    known = query(
        """SELECT DISTINCT et.id, et.title, et.primary_muscle_group
           FROM workout_exercises we
           JOIN exercise_templates et ON et.id = we.exercise_template_id
           ORDER BY et.primary_muscle_group, et.title"""
    ) if include_routine else []

    safe_name = sanitize_for_prompt(name, max_len=60)

    now = datetime.now(timezone.utc).astimezone()
    current_datetime_line = f"- Current date/time: {now.strftime('%A, %Y-%m-%d %H:%M')} (local)"

    # Days since last workout — helps the coach assess recovery state
    last_wkt = query("SELECT start_time FROM workouts ORDER BY start_time DESC LIMIT 1")
    days_since_last = None
    if last_wkt:
        try:
            last_dt = datetime.fromisoformat(last_wkt[0]["start_time"].replace("Z", "+00:00"))
            days_since_last = (datetime.now(timezone.utc) - last_dt).days
        except Exception:
            pass

    lines = [
        f"## Athlete: {safe_name}",
        current_datetime_line,
        f"## Training summary (last {weeks} weeks)\n",
        f"- Total workouts: {freq['total_workouts']}",
        f"- Avg workouts/week: {freq['avg_per_week']}",
        f"- Avg session duration: {freq['avg_duration_minutes']} min",
        f"- Avg rest days between sessions: {freq['rest_day_avg']}",
    ]
    if days_since_last is not None:
        lines.append(f"- Days since last workout: {days_since_last}")
    lines += [
        "",
        "## Weekly volume (avg kg tonnage) by muscle group",
    ]
    for muscle, vol in muscle_vol.items():
        sessions = muscle_freq.get(muscle, 0)
        sets_wk = sets_per_week.get(muscle, 0)
        lines.append(f"  - {muscle}: {vol} kg/week ({sessions:.1f} sessions/wk, {sets_wk:.1f} sets/wk)")

    if body:
        lines += [
            "",
            "## Body measurements",
            f"  - Weight: {body.get('weight_kg')} kg (change: {body.get('weight_change_kg', 'N/A')} kg)",
            f"  - Body fat: {body.get('fat_percent')}% (change: {body.get('fat_change_pct', 'N/A')}%)",
        ]

    if prs:
        lines += ["", "## Personal records set in last 30 days"]
        for pr in prs[:8]:
            lines.append(f"  - {pr['exercise']}: {pr['weight_kg']}kg × {pr['reps']} reps (e1RM {pr['e1rm']} kg) on {pr['date']}")

    if top_gains:
        lines += ["", "## Top improvements this period"]
        for g in top_gains:
            lines.append(f"  - {g['exercise']}: +{g['improvement_pct']}% (e1RM {g['start_e1rm']} → {g['current_e1rm']} kg)")

    if plateaus:
        lines += ["", "## Exercises showing a plateau"]
        for p in plateaus:
            lines.append(f"  - {p['exercise']}: no progress in last {p['sessions_stalled']} sessions (e1RM {p['current_e1rm']} kg)")

    lines += ["", goals_context_for_ai(weeks)]

    # Include current goals with IDs so the AI can reference them for updates
    active_goals = get_goals()
    if active_goals:
        lines += ["", "## Active goals with IDs (use these IDs in manage_goals)"]
        for g in active_goals:
            safe_desc = sanitize_for_prompt(g["description"], max_len=150)
            lines.append(f"  - id={g['id']} | {safe_desc} | target={g['target']} {g.get('unit') or ''}")

    # Fit / recovery data
    try:
        from fit.analytics import fit_context_for_ai
        fit_ctx = fit_context_for_ai(7)
        if "No Google Fit" not in fit_ctx:
            lines += ["", fit_ctx]
    except Exception:
        pass

    # Memories from past conversations
    try:
        from db.memories import memories_as_context
        mem_ctx = memories_as_context()
        if mem_ctx:
            lines += ["", mem_ctx]
    except Exception:
        pass

    # Recent workouts — the actual sessions with exercises and best sets.
    # This is the most important near-term context: what did the athlete do
    # last, how heavy, and how long ago.
    recent_wkts = query(
        f"""SELECT id, title, start_time, end_time
           FROM workouts
           ORDER BY start_time DESC
           LIMIT {'5' if slim else '7'}"""
    )
    if recent_wkts:
        lines += ["", "## Recent workouts (last sessions, newest first)"]
        for w in recent_wkts:
            try:
                start_dt = datetime.fromisoformat(w["start_time"].replace("Z", "+00:00"))
                date_str = start_dt.strftime("%a %d %b %Y")
                end_dt = datetime.fromisoformat(w["end_time"].replace("Z", "+00:00"))
                dur = int((end_dt - start_dt).total_seconds() / 60)
                dur_str = f"{dur} min"
            except Exception:
                date_str = (w["start_time"] or "")[:10]
                dur_str = ""

            lines.append(f"\n  {w['title']} — {date_str} ({dur_str})")

            # Best normal set per exercise in this workout
            ex_rows = query(
                """SELECT we.title,
                          ws.weight_kg,
                          ws.reps,
                          ws.weight_kg * (1 + ws.reps / 30.0) AS e1rm
                   FROM workout_exercises we
                   JOIN workout_sets ws ON ws.workout_exercise_id = we.id
                   WHERE we.workout_id = ?
                     AND ws.type = 'normal'
                     AND ws.weight_kg IS NOT NULL
                     AND ws.reps IS NOT NULL AND ws.reps > 0
                   ORDER BY we.idx, e1rm DESC""",
                (w["id"],),
            )
            # One best set per exercise name
            seen_ex: dict = {}
            for row in ex_rows:
                if row["title"] not in seen_ex:
                    seen_ex[row["title"]] = row

            if seen_ex:
                for ex_title, row in seen_ex.items():
                    lines.append(
                        f"    - {ex_title}: {row['weight_kg']} kg × {row['reps']} reps"
                        f" (e1RM {row['e1rm']:.1f} kg)"
                    )
            else:
                # Bodyweight / cardio session — just list exercise names
                bw = query(
                    "SELECT DISTINCT we.title FROM workout_exercises we WHERE we.workout_id = ?",
                    (w["id"],),
                )
                for b in bw:
                    lines.append(f"    - {b['title']}")

    saved_routines = get_routines_with_exercises() if include_routine else []
    if saved_routines:
        lines += ["", f"## Saved routines ({len(saved_routines)} total)"]
        for r in saved_routines:
            lines.append(f"\n  ### {r['title']} (id: {r['id']})")
            if r.get("notes"):
                lines.append(f"  [notes: {sanitize_for_prompt(r['notes'], max_len=120)}]")
            for ex in r.get("exercises", []):
                normal_sets = [s for s in ex["sets"] if s.get("type") == "normal"]
                set_desc = ""
                if normal_sets:
                    reps_list = [str(s["reps"]) for s in normal_sets if s.get("reps")]
                    weight = next((s["weight_kg"] for s in normal_sets if s.get("weight_kg")), None)
                    count = len(normal_sets)
                    if weight:
                        set_desc = f" — {count}×{reps_list[0] if reps_list else '?'} @ {weight}kg"
                    elif reps_list:
                        set_desc = f" — {count}×{reps_list[0]}"
                lines.append(f"    - {ex['title']}{set_desc}")

    if known:
        lines += ["", "## Exercise library (use these IDs in routines)"]
        for ex in known:
            lines.append(f"  - {ex['title']} | id: {ex['id']} | muscle: {ex['primary_muscle_group']}")

    return "\n".join(lines)


# ── one-shot coaching report ──────────────────────────────────────────────────

_COACH_SYSTEM = ANTI_INJECTION_PREAMBLE + """\
You are an experienced strength and hypertrophy coach with deep knowledge of exercise science.

Base every programming decision on peer-reviewed research and evidence-based principles:
- Progressive overload, specificity, and the SRA (stimulus–recovery–adaptation) cycle.
- Volume landmarks: MEV (minimum effective volume), MAV (maximum adaptive volume), and MRV
  (maximum recoverable volume) as described by Mike Israetel et al.
- Research-backed weekly set ranges for hypertrophy (~10–20 working sets/muscle/week,
  Schoenfeld et al.) and strength (3–5 heavy sets/pattern/week).
- RIR (Reps in Reserve) autoregulation and RPE-based load progression.
- Periodization models (linear, undulating, block) suited to the athlete's experience level.
- Recovery, sleep, and nutrition fundamentals per NSCA, ACSM, and ISSN guidelines.
When making specific programming claims, briefly reference the underlying principle or research
(e.g., "insufficient chest volume per Schoenfeld hypertrophy recommendations…").
Treat the athlete as a professional coaching client: evidence-based, goal-oriented, safety-conscious.

Analyze the athlete's training data, taking their stated goals into account, and return
a JSON response with this exact structure:

{
  "workout_score": <integer 0-100. Score the training quality: deduct for missed sessions vs goals,
    plateaus, imbalanced volume, declining e1RM, poor consistency. 90-100 = excellent, 70-89 = good,
    50-69 = average, below 50 = needs work.>,
  "health_score": <integer 0-100 based on Google Fit data if provided: sleep hours (8h ideal),
    recovery score, resting HR trend, daily steps. If no Fit data is in the context, set to null.>,
  "combined_score": <integer 0-100. If health_score is not null: workout_score*0.7 + health_score*0.3,
    rounded. If health_score is null: equal to workout_score.>,
  "strengths": ["<observation>", ...],
  "weaknesses": ["<observation>", ...],
  "recommendations": ["<actionable tip>", ...],
  "next_focus": "<what to prioritize in the next 2-4 weeks>",
  "routine": {
    "title": "<routine name>",
    "notes": "<coaching notes for the routine>",
    "exercises": [
      {
        "exercise_template_id": "<id from the exercise library>",
        "title": "<exercise name>",
        "rest_seconds": 90,
        "notes": "<HOW TO PERFORM: step-by-step execution cues. ATTENTION: key form points, safety tips, and common mistakes to avoid>",
        "sets": [
          {"type": "warmup", "weight_kg": null, "reps": 10},
          {"type": "normal", "weight_kg": <number>, "reps": <number>}
        ]
      }
    ]
  }
}

Rules:
- Tailor recommendations to the athlete's stated goals and memories from past conversations.
- Only use exercise_template_ids from the "Exercise library" section.
- The routine should target 4-6 exercises and address identified weaknesses.
- Set weights should reflect the athlete's current strength level.
- Every exercise MUST have a notes field with execution instructions and attention points.
- Return ONLY the JSON object, no markdown fences or extra text.\
"""


def get_coaching(weeks: int = 8, generate_routine: bool = False) -> dict:
    from debug_log import log
    import config as _cfg
    from db.goals import get_token_usage as _get_usage
    _tokens_before = _get_usage()
    # The athlete's existing routines stay in the context by default — they help the
    # analysis and any routine edits — and this is configurable. Generating a NEW
    # routine, on the other hand, is always an explicit request.
    include_routines_ctx = (get_pref("ai_include_routines") != "0") or generate_routine
    log("AI", "Coaching report started", provider=_cfg.AI_PROVIDER, model=_cfg.AI_MODEL,
        weeks=weeks, generate_routine=generate_routine, routines_in_context=include_routines_ctx)
    context = _build_context(weeks, include_routine=include_routines_ctx)
    lang = get_pref("ai_language") or "English"
    lang_line = f"\nAlways respond entirely in {_ai_lang_instruction(lang)}.\n" if lang != "English" else ""
    ask_line = (
        "Please analyse my training and generate a suggested next routine."
        if generate_routine
        else "Please analyse my training. Do NOT generate a routine."
    )
    # The training data goes in the (cacheable) system block rather than the user
    # message, so repeated reports within the cache TTL reuse it as a cache hit.
    routine_rule = "" if generate_routine else (
        '\nIMPORTANT: The athlete did not request a routine. Omit the "routine" field '
        "entirely (set it to null) and do not generate any exercises.\n"
    )
    system = (
        f"{_COACH_SYSTEM}{lang_line}{routine_rule}"
        "\n\n<training_data>\n"
        f"{context}\n"
        "</training_data>"
    )
    prompt = ask_line

    console.print(_("coach.powered_by", provider=provider_label()))

    # A routine adds many exercises to the JSON; plain analysis is far smaller.
    report_max_tokens = 4096 if generate_routine else 2048
    with console.status(_("coach.generating"), spinner="dots"):
        result = complete_json(prompt, system=system, max_tokens=report_max_tokens)

    _tokens_after = _get_usage()
    log("AI", "Coaching report complete",
        input=_tokens_after["input"] - _tokens_before["input"],
        output=_tokens_after["output"] - _tokens_before["output"],
        cache_read=_tokens_after["cache_read"] - _tokens_before["cache_read"])
    return result


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


# ── tools ─────────────────────────────────────────────────────────────────────

_CHAT_SYSTEM_BASE = (
    ANTI_INJECTION_PREAMBLE
    + "You are a personal fitness coach assistant with deep knowledge of exercise science. "
    "You have the athlete's complete training history, their stated goals, and memories from previous conversations.\n"
    "Ground every recommendation in evidence-based principles: progressive overload, SRA cycle, "
    "MEV/MAV/MRV volume landmarks (Israetel et al.), RIR autoregulation, periodization models, "
    "and NSCA/ACSM/ISSN guidelines. When making specific programming claims, briefly reference "
    "the underlying research principle.\n"
    "Answer questions conversationally and reference their actual numbers.\n"
    "Be encouraging but honest. Keep answers concise unless asked to elaborate.\n"
    "TOOL USE RULES — follow these exactly:\n"
    "- When the athlete asks you to create, send, push, or build a new routine, you MUST call the "
    "push_routine tool immediately. Do NOT describe or list the routine in plain text. Just call the tool.\n"
    "- When the athlete asks you to update, edit, modify, or change an existing routine, you MUST call "
    "the update_routine tool using the routine_id from the Saved routines section. "
    "Do NOT describe changes in plain text. Just call the tool.\n"
    "- When the athlete explicitly asks to change, add, or remove a goal, you MUST call the "
    "manage_goals tool — always describe the exact change in changes_summary so the user can confirm.\n"
    "- Never simulate tool actions in text. If an action requires a tool, call the tool.\n"
    "Only use exercise_template_ids from the exercise library provided.\n"
    "Address the athlete by their name when appropriate.\n"
    "EXERCISE NOTES RULES — for every exercise in any routine you create or update:\n"
    "- notes field MUST contain: step-by-step execution instructions followed by key attention points "
    "(form cues, safety tips, common mistakes to avoid)."
)

_PUSH_ROUTINE_TOOL: dict = {
    "name": "push_routine",
    "description": "Push a new workout routine to the user's Hevy app.",
    "parameters": {
        "type": "object",
        "required": ["title", "exercises"],
        "properties": {
            "title": {"type": "string"},
            "notes": {"type": "string"},
            "exercises": {
                "type": "array",
                "description": (
                    "List of exercises. Each exercise object: "
                    "{exercise_template_id: string (from library), title: string, "
                    "rest_seconds: integer, "
                    "notes: string (REQUIRED: step-by-step execution instructions + key attention points for form/safety), "
                    "sets: [{type: 'warmup'|'normal'|'failure'|'dropset', weight_kg: number, reps: integer}]}"
                ),
                "items": {"type": "object"},
            },
        },
    },
}

_UPDATE_ROUTINE_TOOL: dict = {
    "name": "update_routine",
    "description": "Update an existing workout routine in the user's Hevy app.",
    "parameters": {
        "type": "object",
        "required": ["routine_id", "title", "exercises"],
        "properties": {
            "routine_id": {
                "type": "string",
                "description": "ID of the routine to update (from the Saved routines section)",
            },
            "title": {"type": "string"},
            "notes": {"type": "string"},
            "exercises": {
                "type": "array",
                "description": (
                    "Complete updated exercise list. Each exercise object: "
                    "{exercise_template_id: string (from library), title: string, "
                    "rest_seconds: integer, "
                    "notes: string (REQUIRED: step-by-step execution instructions + key attention points for form/safety), "
                    "sets: [{type: 'warmup'|'normal'|'failure'|'dropset', weight_kg: number, reps: integer}]}"
                ),
                "items": {"type": "object"},
            },
        },
    },
}

_MANAGE_GOALS_TOOL: dict = {
    "name": "manage_goals",
    "description": (
        "Add, update, or remove a training goal. Use when the athlete explicitly asks to "
        "change, add, or remove a goal. Always describe what will change in changes_summary."
    ),
    "parameters": {
        "type": "object",
        "required": ["action", "changes_summary"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "update", "remove"],
            },
            "changes_summary": {
                "type": "string",
                "description": "Exact description of the change shown to the user for confirmation",
            },
            "goal_id": {
                "type": "integer",
                "description": "ID of the goal to update or remove (from the active goals list)",
            },
            "goal_type": {
                "type": "string",
                "enum": ["lift_pr", "frequency", "weight_loss", "weight_gain", "body_fat", "volume", "custom"],
            },
            "description": {"type": "string", "description": "Human-readable goal label"},
            "target": {"type": "number"},
            "unit": {"type": "string"},
            "exercise_template_id": {"type": "string"},
            "exercise_name": {"type": "string"},
            "muscle_group": {"type": "string"},
        },
    },
}


# ── tool handlers ─────────────────────────────────────────────────────────────

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
        ANTI_INJECTION_PREAMBLE
        + "You are a strength and hypertrophy coach. For each exercise, write 2-3 sentences "
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
        ex for ex in exercises
        if isinstance(ex, dict)
        and not (ex.get("benefits") or "").strip()
        and (ex.get("title") or ex.get("exercise_template_id"))
    ]
    generated: dict = {}
    if missing:
        with console.status(_("coach.generating_benefits"), spinner="dots"):
            generated = _generate_benefits(missing)

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
        console.print(Panel(
            "\n".join(benefit_lines).strip(),
            title=_("chat.exercise_benefits_title"),
            border_style="green",
        ))


def _show_and_confirm_routine(routine: dict) -> dict:
    """Show the proposed routine, ask for confirmation, push if approved. Returns tool result."""
    from hevy.client import HevyClient

    lines = [f"[bold]{routine.get('title')}[/bold]"]
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

    console.print(Panel("\n".join(lines), title=_("chat.routine_panel_title"), border_style="cyan"))

    invalid_ids = [
        ex.get("exercise_template_id", "")
        for ex in routine.get("exercises", [])
        if isinstance(ex, dict) and ex.get("exercise_template_id")
        and not query(
            "SELECT 1 FROM exercise_templates WHERE id = ?",
            (ex["exercise_template_id"],),
        )
    ]
    if invalid_ids:
        console.print(_("chat.routine_invalid_ids", count=len(invalid_ids), ids=', '.join(invalid_ids[:3])))

    if not questionary.confirm(_("chat.push_routine_prompt"), default=True).ask():
        from debug_log import log
        log("AI", "Routine push declined by user")
        console.print(_("chat.routine_not_pushed"))
        return {"success": False, "message": "User declined"}

    try:
        with console.status(_("chat.saving_routine"), spinner="dots"):
            from hevy.client import _routine_id
            from debug_log import log
            resp = HevyClient().create_routine(_stamp_routine(routine))
            routine_id = _routine_id(resp)
        log("AI", "Routine pushed to Hevy", routine_id=routine_id,
            exercises=len(routine.get("exercises", [])))
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
    from hevy.client import HevyClient
    from db.store import upsert_routine

    routine_id = str(fc_args.get("routine_id", ""))
    new_routine = {
        "title": fc_args.get("title"),
        "notes": fc_args.get("notes"),
        "exercises": fc_args.get("exercises", []),
    }

    # Look up current routine name from DB for reference
    current_routines = get_routines_with_exercises()
    current = next((r for r in current_routines if str(r["id"]) == routine_id), None)
    current_title = current["title"] if current else routine_id

    lines = [f"[bold]{new_routine.get('title')}[/bold]  [dim](updating: {current_title})[/dim]"]
    if new_routine.get("notes"):
        lines.append(f"[dim]{new_routine['notes']}[/dim]")
    lines.append("")
    for ex in new_routine.get("exercises", []):
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

    console.print(Panel("\n".join(lines), title=_("chat.routine_update_panel_title"), border_style="yellow"))

    invalid_ids = [
        ex.get("exercise_template_id", "")
        for ex in new_routine.get("exercises", [])
        if isinstance(ex, dict) and ex.get("exercise_template_id")
        and not query("SELECT 1 FROM exercise_templates WHERE id = ?", (ex["exercise_template_id"],))
    ]
    if invalid_ids:
        console.print(_("chat.routine_invalid_ids", count=len(invalid_ids), ids=', '.join(invalid_ids[:3])))

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


def _handle_manage_goals(fc_args: dict) -> dict:
    """Handle a goal add/update/remove request. Returns tool result."""
    from db.goals import save_goal, delete_goal, update_goal_fields, get_goals

    action = fc_args.get("action")
    summary = fc_args.get("changes_summary", "Modify a goal")

    if action in ("update", "remove"):
        gid = fc_args.get("goal_id")
        valid_ids = {g["id"] for g in get_goals()}
        if gid is None or int(gid) not in valid_ids:
            console.print(_("chat.goal_invalid_id", gid=gid))
            return {"success": False, "error": f"Goal ID {gid} does not exist"}

    console.print(Panel(
        f"[bold]{sanitize_for_prompt(summary, max_len=200)}[/bold]",
        title=_("chat.goal_panel_title"),
        border_style="yellow",
    ))

    if not questionary.confirm(_("chat.apply_change_prompt"), default=True).ask():
        from debug_log import log
        log("AI", "Goal change declined by user", action=action)
        console.print(_("chat.change_not_applied"))
        return {"success": False, "message": "User declined"}

    try:
        with console.status(_("chat.applying_change"), spinner="dots"):
            if action == "add":
                save_goal(
                    type=fc_args.get("goal_type", "custom"),
                    description=fc_args.get("description", ""),
                    target=fc_args.get("target"),
                    unit=fc_args.get("unit"),
                    exercise_template_id=fc_args.get("exercise_template_id"),
                    exercise_name=fc_args.get("exercise_name"),
                    muscle_group=fc_args.get("muscle_group"),
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


# ── weak-model tool-call nudge ────────────────────────────────────────────────

_ROUTINE_SIGNALS = ["sets", "reps", "treino", "workout", "routine", "exercício", "exercise",
                    "warmup", "normal", "dropset", "kg×", "kg x", "agachamento", "supino",
                    "remada", "rosca", "tríceps", "desenvolvimento", "levantamento"]
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


# ── memory extraction ─────────────────────────────────────────────────────────

_MEMORY_SYSTEM = (
    "You extract memorable fitness coaching facts from conversations. "
    "Return ONLY a JSON array of strings, no markdown fences or extra text."
)

_MEMORY_PROMPT = """\
Review this fitness coaching conversation and extract facts worth remembering for future sessions.

Extract ONLY:
- User preferences (exercises liked/disliked, equipment, training time/location)
- Physical limitations, injuries, or health conditions mentioned
- Personal context affecting training (schedule, stress, sleep issues, job)
- Explicit feedback on recommendations ("tried X, it didn't work because...")
- Strong opinions about training style, intensity, or volume

Do NOT extract: general Q&A, stats, routine details, or things obvious from the training data.

Return a JSON array of concise strings (max 2 sentences each). Return [] if nothing memorable.

Conversation:
"""


def _extract_and_save_memories(conversation_log: list[dict]) -> int:
    """Extract key facts from the conversation and persist them."""
    if len(conversation_log) < 2:
        return 0

    text_messages = [
        m for m in conversation_log if isinstance(m.get("content"), str)
    ]
    if len(text_messages) < 2:
        return 0

    conv_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in text_messages
    )
    if len(conv_text) < 150:
        return 0

    try:
        full_text = ""
        for chunk in stream_complete(_MEMORY_PROMPT + conv_text[:5000], system=_MEMORY_SYSTEM,
                                     max_tokens=1024):
            full_text += chunk

        raw = full_text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

        memories = json.loads(raw)
        if not isinstance(memories, list):
            return 0

        from db.memories import save_memory
        saved = 0
        for mem in memories[:8]:
            if isinstance(mem, str) and len(mem.strip()) > 15:
                # Sanitize before storing — prevents injected text from
                # persisting as a "memory" across future sessions.
                clean = sanitize_for_prompt(mem.strip(), max_len=300)
                if clean:
                    save_memory(clean)
                saved += 1
        return saved
    except Exception:
        return 0


# ── enhanced chat ─────────────────────────────────────────────────────────────

def start_enhanced_chat(weeks: int = 8) -> None:
    """Interactive chat with tool calling, goal management, and memory persistence."""
    from debug_log import log as _log
    import config as _cfg

    slim = get_pref("ai_chat_slim") != "0"  # default True unless explicitly disabled
    # The athlete's saved routines are included by default (helps the coach create,
    # edit, and analyse routines); configurable via Settings → AI. Creating a routine
    # still requires an explicit request — the push_routine tool only fires when asked.
    include_routines = get_pref("ai_include_routines") != "0"
    context = _build_context(weeks, slim=slim, include_routine=include_routines)
    _log("AI", "Chat session started",
         provider=_cfg.AI_PROVIDER, model=_cfg.AI_MODEL, weeks=weeks,
         slim=slim, lang=get_pref("ai_language") or "English")
    lang = get_pref("ai_language") or "English"
    lang_line = f"\nAlways respond entirely in {_ai_lang_instruction(lang)}.\n" if lang != "English" else ""
    # Use XML-like delimiters so the model can clearly distinguish
    # instructions (above) from untrusted data (below).
    system = (
        f"{_CHAT_SYSTEM_BASE}{lang_line}\n\n"
        "<training_data>\n"
        f"{context}\n"
        "</training_data>"
    )

    session = create_chat_session(system=system, tools=[_PUSH_ROUTINE_TOOL, _UPDATE_ROUTINE_TOOL, _MANAGE_GOALS_TOOL])

    console.rule(_("chat.rule_title"))
    console.print(_("chat.hint", provider=provider_label(), weeks=weeks))

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
        if user_input.lower() in ("quit", "exit", "q", "bye", "sair", "voltar", "menu"):
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
        if response.tool_calls:
            tool_results: list[tuple] = []
            for tc in response.tool_calls:
                _log("AI", f"Tool call: {tc.name}")
                if tc.name == "push_routine":
                    result = _show_and_confirm_routine(dict(tc.args))
                elif tc.name == "update_routine":
                    result = _show_and_confirm_routine_update(dict(tc.args))
                elif tc.name == "manage_goals":
                    result = _handle_manage_goals(dict(tc.args))
                else:
                    result = {"error": f"Unknown tool: {tc.name}"}
                tool_results.append((tc, result))

            try:
                with console.status(_("chat.thinking_short"), spinner="dots"):
                    follow = session.submit_tool_results(tool_results)
            except KeyboardInterrupt:
                console.print(_("chat.cancelled"))
                continue
            except Exception as e:
                console.print(f"[red]{_friendly_error(e)}[/red]\n")
                continue

            if follow.text:
                console.print(_("chat.coach_label"))
                console.print(Markdown(follow.text))
                console.print()
                conversation_log.append({"role": "assistant", "content": follow.text})

    try:
        readline.write_history_file(_CHAT_HISTORY_FILE)
    except OSError:
        pass

    # ── log session totals ────────────────────────────────────────────────────
    _log("AI", "Chat session ended",
         turns=len([m for m in conversation_log if m["role"] == "user"]))

    # ── extract and save memories after session ends ──
    if len(conversation_log) >= 2:
        with console.status(_("chat.saving_insights"), spinner="dots"):
            saved = _extract_and_save_memories(conversation_log)
        _log("AI", "Memories extracted", saved=saved)
        if saved > 0:
            console.print(_("chat.insights_saved", count=saved))
        else:
            console.print(_("chat.no_insights"))
