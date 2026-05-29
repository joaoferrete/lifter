"""AI coaching via Gemini — analyzes workout data and generates suggestions."""
import json

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL
from analytics.volume import muscle_group_summary, sets_per_muscle_per_week
from analytics.progression import detect_plateaus, top_progressions
from analytics.frequency import workout_frequency, muscle_group_frequency
from analytics.records import all_time_records, recent_prs, body_measurement_trend
from db.store import query


def _build_context(weeks: int = 8) -> str:
    freq = workout_frequency(weeks)
    muscle_vol = muscle_group_summary(weeks)
    muscle_freq = muscle_group_frequency(weeks)
    sets_per_week = sets_per_muscle_per_week(weeks)
    plateaus = detect_plateaus(weeks)
    top_gains = top_progressions(weeks)
    prs = recent_prs(30)
    body = body_measurement_trend(weeks)

    # Build known exercises list (exercises user has done, with IDs)
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
        lines += ["", "## Exercise library (exercises I've done — use these template_ids in the routine)"]
        for ex in known:
            lines.append(f"  - {ex['title']} | id: {ex['id']} | muscle: {ex['primary_muscle_group']}")

    return "\n".join(lines)


_SYSTEM_PROMPT = """You are an experienced strength and hypertrophy coach.
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
                system_instruction=_SYSTEM_PROMPT,
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
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.4,
            ),
        )
        full_text = response.text

    # Strip markdown fences if model wrapped the JSON
    raw = full_text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    return json.loads(raw)


def push_routine_to_hevy(routine_data: dict) -> dict:
    """Push an AI-generated routine dict to Hevy via the API."""
    from hevy.client import HevyClient
    client = HevyClient()
    return client.create_routine(routine_data)


_CHAT_SYSTEM = """\
You are a personal fitness coach assistant. The athlete's recent training data is provided.
Answer questions conversationally and reference their actual numbers when relevant.
Be encouraging but honest. Keep answers concise unless asked to elaborate.
When suggesting workouts, use exercises the athlete has already done."""


def start_chat(weeks: int = 8) -> None:
    """Interactive chat loop with Gemini about the user's training."""
    from rich.console import Console
    from rich.markdown import Markdown

    console = Console()
    context = _build_context(weeks)

    system = f"{_CHAT_SYSTEM}\n\n--- TRAINING DATA ---\n{context}\n--- END DATA ---"

    client = genai.Client(api_key=GEMINI_API_KEY)
    chat = client.chats.create(
        model=GEMINI_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.7,
        ),
    )

    while True:
        try:
            user_input = console.input("[bold green]You:[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]See you at the gym![/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q", "bye", "sair"):
            console.print("[dim]See you at the gym![/dim]")
            break

        console.print("\n[bold cyan]Coach:[/bold cyan]")
        try:
            full_response = ""
            for chunk in chat.send_message_stream(user_input):
                text = chunk.text or ""
                print(text, end="", flush=True)
                full_response += text
            print("\n")
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]\n")
