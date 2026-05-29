"""AI coaching — analyzes workout data and generates suggestions."""
import json

import questionary
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from ai.provider import create_chat_session, stream_complete, provider_label, ToolCall
from analytics.volume import muscle_group_summary, sets_per_muscle_per_week
from analytics.progression import detect_plateaus, top_progressions
from analytics.frequency import workout_frequency, muscle_group_frequency
from analytics.records import all_time_records, recent_prs, body_measurement_trend
from db.store import query
from db.goals import goals_context_for_ai, get_pref

console = Console()

# ── context builder ───────────────────────────────────────────────────────────

def _build_context(weeks: int = 8) -> str:
    name = get_pref("display_name") or "the athlete"
    freq = workout_frequency(weeks)
    muscle_vol = muscle_group_summary(weeks)
    muscle_freq = muscle_group_frequency(weeks)
    sets_per_week = sets_per_muscle_per_week(weeks)
    plateaus = detect_plateaus(weeks)
    top_gains = top_progressions(weeks)
    prs = recent_prs(30)
    body = body_measurement_trend(weeks)

    known = query(
        """SELECT DISTINCT et.id, et.title, et.primary_muscle_group
           FROM workout_exercises we
           JOIN exercise_templates et ON et.id = we.exercise_template_id
           ORDER BY et.primary_muscle_group, et.title"""
    )

    lines = [
        f"## Athlete: {name}",
        f"## Training summary (last {weeks} weeks)\n",
        f"- Total workouts: {freq['total_workouts']}",
        f"- Avg workouts/week: {freq['avg_per_week']}",
        f"- Avg session duration: {freq['avg_duration_minutes']} min",
        f"- Avg rest days between sessions: {freq['rest_day_avg']}",
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

    try:
        from fit.analytics import fit_context_for_ai
        fit_ctx = fit_context_for_ai(7)
        if "No Google Fit" not in fit_ctx:
            lines += ["", fit_ctx]
    except Exception:
        pass

    if known:
        lines += ["", "## Exercise library (use these IDs in routines)"]
        for ex in known:
            lines.append(f"  - {ex['title']} | id: {ex['id']} | muscle: {ex['primary_muscle_group']}")

    return "\n".join(lines)


# ── one-shot coaching report ──────────────────────────────────────────────────

_COACH_SYSTEM = """You are an experienced strength and hypertrophy coach.
Analyze the athlete's training data, taking their stated goals into account, and return
a JSON response with this exact structure:

{
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
        "notes": "<cue or note>",
        "sets": [
          {"type": "warmup", "weight_kg": null, "reps": 10},
          {"type": "normal", "weight_kg": <number>, "reps": <number>}
        ]
      }
    ]
  }
}

Rules:
- Tailor recommendations to the athlete's stated goals.
- Only use exercise_template_ids from the "Exercise library" section.
- The routine should target 4-6 exercises and address identified weaknesses.
- Set weights should reflect the athlete's current strength level.
- Return ONLY the JSON object, no markdown fences or extra text."""


def get_coaching(weeks: int = 8) -> dict:
    context = _build_context(weeks)
    prompt = f"{context}\n\nPlease analyse my training and generate a suggested next routine."

    console.print(f"\n[dim]Thinking via {provider_label()}...[/dim]\n")
    full_text = ""
    for chunk in stream_complete(prompt, system=_COACH_SYSTEM):
        print(chunk, end="", flush=True)
        full_text += chunk
    print()

    raw = full_text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(raw)


def push_routine_to_hevy(routine_data: dict) -> dict:
    from hevy.client import HevyClient
    return HevyClient().create_routine(routine_data)


# ── enhanced chat ─────────────────────────────────────────────────────────────

_CHAT_SYSTEM_BASE = """\
You are a personal fitness coach assistant. You have the athlete's complete training history
and their stated goals. Answer questions conversationally and reference their actual numbers.
Be encouraging but honest. Keep answers concise unless asked to elaborate.
When the athlete asks you to create, build, add, or push a routine or training plan, use the
push_routine tool — don't just describe it, call the function to save it to their app.
Only use exercise_template_ids from the exercise library provided.
Address the athlete by their name when appropriate."""

_PUSH_ROUTINE_TOOL: dict = {
    "name": "push_routine",
    "description": (
        "Push a new workout routine to the user's Hevy app. "
        "Call this when the user asks to create, save, add, or push a routine."
    ),
    "parameters": {
        "type": "object",
        "required": ["title", "exercises"],
        "properties": {
            "title": {"type": "string"},
            "notes": {"type": "string"},
            "exercises": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["exercise_template_id", "title", "sets"],
                    "properties": {
                        "exercise_template_id": {"type": "string"},
                        "title": {"type": "string"},
                        "rest_seconds": {"type": "integer"},
                        "notes": {"type": "string"},
                        "sets": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string", "enum": ["warmup", "normal", "failure", "dropset"]},
                                    "weight_kg": {"type": "number"},
                                    "reps": {"type": "integer"},
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


def _show_and_confirm_routine(routine: dict, session, tool_call: ToolCall) -> None:
    from hevy.client import HevyClient

    lines = [f"[bold]{routine.get('title')}[/bold]"]
    if routine.get("notes"):
        lines.append(f"[dim]{routine['notes']}[/dim]")
    lines.append("")
    for ex in routine.get("exercises", []):
        sets_desc = "  ".join(
            f"[dim]{s.get('type', 'normal')}[/dim] {s.get('weight_kg') or 'BW'}kg×{s.get('reps', '?')}"
            for s in ex.get("sets", [])
        )
        note = f"\n    [dim italic]{ex['notes']}[/dim italic]" if ex.get("notes") else ""
        lines.append(f"  • [bold]{ex['title']}[/bold]  {sets_desc}{note}")

    console.print(Panel("\n".join(lines), title="[bold cyan]Proposed routine[/bold cyan]", border_style="cyan"))

    confirm = questionary.confirm("  Push this routine to your Hevy app?", default=True).ask()

    if confirm:
        try:
            resp = HevyClient().create_routine(routine)
            routine_id = resp.get("routine", {}).get("id", "")
            follow = session.submit_tool_result(tool_call, {"success": True, "routine_id": routine_id})
            console.print(f"[green]✓ Routine saved to Hevy[/green] (id: {routine_id})\n")
            if follow.text:
                console.print(Markdown(follow.text))
                console.print()
        except Exception as e:
            follow = session.submit_tool_result(tool_call, {"success": False, "error": str(e)})
            console.print(f"[red]Failed: {e}[/red]\n")
            if follow.text:
                console.print(Markdown(follow.text))
    else:
        follow = session.submit_tool_result(
            tool_call, {"success": False, "message": "User chose not to push the routine"}
        )
        console.print("[dim]Routine not pushed.[/dim]\n")
        if follow.text:
            console.print(Markdown(follow.text))
            console.print()


def start_enhanced_chat(weeks: int = 8) -> None:
    """Interactive chat with tool calling — the coach can push routines to Hevy."""
    context = _build_context(weeks)
    system = f"{_CHAT_SYSTEM_BASE}\n\n--- TRAINING DATA ---\n{context}\n--- END DATA ---"

    session = create_chat_session(system=system, tools=[_PUSH_ROUTINE_TOOL])

    console.rule("[bold cyan]Chat with AI Coach[/bold cyan]")
    console.print(
        f"  [dim]Provider: {provider_label()} · {weeks} weeks of context loaded.[/dim]\n"
        "  [dim]Ask anything. The coach can create routines directly in your Hevy app.[/dim]\n"
        "  [dim]Type [bold]quit[/bold] or press Ctrl+C to return to the menu.[/dim]\n"
    )

    while True:
        try:
            user_input = console.input("[bold green]You:[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Returning to menu...[/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q", "bye", "sair", "voltar", "menu"):
            break

        console.print()
        try:
            response = session.send(user_input)

            if response.text:
                console.print("[bold cyan]Coach:[/bold cyan]")
                console.print(Markdown(response.text))
                console.print()

            for tc in response.tool_calls:
                if tc.name == "push_routine":
                    _show_and_confirm_routine(dict(tc.args), session, tc)

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]\n")
