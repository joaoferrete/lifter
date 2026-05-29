"""AI coaching via Gemini — analyzes workout data and generates suggestions."""
import json

import questionary
from google import genai
from google.genai import types
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from config import GEMINI_API_KEY, GEMINI_MODEL
from analytics.volume import muscle_group_summary, sets_per_muscle_per_week
from analytics.progression import detect_plateaus, top_progressions
from analytics.frequency import workout_frequency, muscle_group_frequency
from analytics.records import all_time_records, recent_prs, body_measurement_trend
from db.store import query

console = Console()

# ── context builder ───────────────────────────────────────────────────────────

def _build_context(weeks: int = 8) -> str:
    freq = workout_frequency(weeks)
    muscle_vol = muscle_group_summary(weeks)
    muscle_freq = muscle_group_frequency(weeks)
    sets_per_week = sets_per_muscle_per_week(weeks)
    plateaus = detect_plateaus(weeks)
    top_gains = top_progressions(weeks)
    prs = recent_prs(30)
    body = body_measurement_trend(weeks)

    known = query(
        """
        SELECT DISTINCT et.id, et.title, et.primary_muscle_group
        FROM workout_exercises we
        JOIN exercise_templates et ON et.id = we.exercise_template_id
        ORDER BY et.primary_muscle_group, et.title
        """
    )

    lines = [
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

    if known:
        lines += ["", "## Exercise library (use these IDs in routines)"]
        for ex in known:
            lines.append(f"  - {ex['title']} | id: {ex['id']} | muscle: {ex['primary_muscle_group']}")

    return "\n".join(lines)


# ── one-shot coaching report ──────────────────────────────────────────────────

_COACH_SYSTEM = """You are an experienced strength and hypertrophy coach.
Analyze the athlete's training data and return a JSON response with this exact structure:

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
- Only use exercise_template_ids from the "Exercise library" section of the data.
- The routine should target 4-6 exercises and address identified weaknesses.
- Set weights should reflect the athlete's current strength level based on their records.
- Be specific and evidence-based in your analysis.
- Return ONLY the JSON object, no markdown fences or extra text."""


def get_coaching(weeks: int = 8, stream: bool = True) -> dict:
    context = _build_context(weeks)
    prompt = f"{context}\n\nPlease analyse my training and generate a suggested next routine."

    client = genai.Client(api_key=GEMINI_API_KEY)

    if stream:
        print("\n[Gemini is thinking...]\n")
        full_text = ""
        for chunk in client.models.generate_content_stream(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_COACH_SYSTEM,
                temperature=0.4,
            ),
        ):
            text = chunk.text or ""
            print(text, end="", flush=True)
            full_text += text
        print()
    else:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_COACH_SYSTEM,
                temperature=0.4,
            ),
        )
        full_text = response.text

    raw = full_text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    return json.loads(raw)


def push_routine_to_hevy(routine_data: dict) -> dict:
    from hevy.client import HevyClient
    return HevyClient().create_routine(routine_data)


# ── enhanced chat with tool calling ──────────────────────────────────────────

_CHAT_SYSTEM = """\
You are a personal fitness coach assistant. You have the athlete's complete training history.
Answer questions conversationally and reference their actual numbers.
Be encouraging but honest. Keep answers concise unless asked to elaborate.
When the athlete asks you to create, build, add, or push a routine or workout plan, use the
push_routine tool — don't just describe it, actually call the function to save it to their app.
Only use exercise_template_ids from the exercise library provided."""

_PUSH_ROUTINE_TOOL = {
    "name": "push_routine",
    "description": (
        "Push a new workout routine to the user's Hevy app. "
        "Call this whenever the user asks to create, save, add, or push a routine or training plan."
    ),
    "parameters": {
        "type": "object",
        "required": ["title", "exercises"],
        "properties": {
            "title": {"type": "string", "description": "Name of the routine"},
            "notes": {"type": "string", "description": "General coaching notes for the routine"},
            "exercises": {
                "type": "array",
                "description": "Ordered list of exercises",
                "items": {
                    "type": "object",
                    "required": ["exercise_template_id", "title", "sets"],
                    "properties": {
                        "exercise_template_id": {
                            "type": "string",
                            "description": "ID from the exercise library — must match exactly",
                        },
                        "title": {"type": "string"},
                        "rest_seconds": {"type": "integer", "description": "Rest between sets in seconds"},
                        "notes": {"type": "string"},
                        "sets": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "enum": ["warmup", "normal", "failure", "dropset"],
                                    },
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


def _confirm_and_push(fc_args: dict, chat_session) -> None:
    """Show the proposed routine, ask for confirmation, and push if approved."""
    from hevy.client import HevyClient

    routine = dict(fc_args)
    title = routine.get("title", "Untitled")

    lines = [f"[bold]{title}[/bold]"]
    if routine.get("notes"):
        lines.append(f"[dim]{routine['notes']}[/dim]")
    lines.append("")
    for ex in routine.get("exercises", []):
        sets_desc = "  ".join(
            f"[dim]{s.get('type', 'normal')}[/dim] "
            f"{s.get('weight_kg') or 'BW'}kg×{s.get('reps', '?')}"
            for s in ex.get("sets", [])
        )
        note = f"  [dim italic]{ex['notes']}[/dim italic]" if ex.get("notes") else ""
        lines.append(f"  • [bold]{ex['title']}[/bold]  {sets_desc}{note}")

    console.print(Panel("\n".join(lines), title="[bold cyan]Proposed routine[/bold cyan]", border_style="cyan"))

    confirm = questionary.confirm("  Push this routine to your Hevy app?", default=True).ask()

    if confirm:
        try:
            resp = HevyClient().create_routine(routine)
            routine_id = resp.get("routine", {}).get("id", "")
            follow_up = chat_session.send_message(
                types.Part.from_function_response(
                    name="push_routine",
                    response={"success": True, "routine_id": routine_id},
                )
            )
            console.print(f"[green]✓ Routine saved to Hevy[/green] (id: {routine_id})\n")
            if follow_up.text:
                console.print(Markdown(follow_up.text))
                console.print()
        except Exception as e:
            chat_session.send_message(
                types.Part.from_function_response(
                    name="push_routine",
                    response={"success": False, "error": str(e)},
                )
            )
            console.print(f"[red]Failed to push routine: {e}[/red]\n")
    else:
        follow_up = chat_session.send_message(
            types.Part.from_function_response(
                name="push_routine",
                response={"success": False, "message": "User chose not to push the routine"},
            )
        )
        console.print("[dim]Routine not pushed.[/dim]\n")
        if follow_up.text:
            console.print(Markdown(follow_up.text))
            console.print()


def start_enhanced_chat(weeks: int = 8) -> None:
    """Interactive chat with tool calling — the AI can push routines to Hevy."""
    context = _build_context(weeks)
    system = f"{_CHAT_SYSTEM}\n\n--- TRAINING DATA ---\n{context}\n--- END DATA ---"

    client = genai.Client(api_key=GEMINI_API_KEY)
    chat = client.chats.create(
        model=GEMINI_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[_PUSH_ROUTINE_TOOL])],
            temperature=0.7,
        ),
    )

    console.rule("[bold cyan]Chat with AI Coach[/bold cyan]")
    console.print(
        f"  [dim]Context: {weeks} weeks of training data loaded.[/dim]\n"
        "  [dim]Ask anything. The coach can also create routines directly in your Hevy app.[/dim]\n"
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
            response = chat.send_message(user_input)

            # Handle each part — text or function call
            parts = response.candidates[0].content.parts if response.candidates else []
            has_function_call = False

            for part in parts:
                if part.text:
                    console.print("[bold cyan]Coach:[/bold cyan]")
                    console.print(Markdown(part.text))
                    console.print()
                if hasattr(part, "function_call") and part.function_call:
                    has_function_call = True
                    fc = part.function_call
                    if fc.name == "push_routine":
                        _confirm_and_push(dict(fc.args), chat)

            # Fallback if no parts had text or function call
            if not parts and response.text:
                console.print("[bold cyan]Coach:[/bold cyan]")
                console.print(Markdown(response.text))
                console.print()

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]\n")
