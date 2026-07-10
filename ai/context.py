"""Prompt-context formatters — turn stored data into text for the AI system prompt.

These live in the AI layer (not in db/) because they are presentation concerns:
they decide what the model sees, apply prompt-injection sanitization, and know
nothing about persistence beyond the public db APIs they call.
"""

from ai.sanitize import sanitize_for_prompt
from db.goals import get_goals
from db.memories import MEMORY_SUMMARY_MAX_LEN, get_recent_memories


def goals_context_for_ai(weeks: int = 8) -> str:
    """Return a text summary of goals + current progress for the AI system prompt."""
    goals = get_goals()
    if not goals:
        return "No goals set."

    from analytics.goal_progress import compute_goal_progress

    progress = compute_goal_progress()
    prog_by_id = {p["id"]: p for p in progress}

    lines = ["## User goals"]
    for g in goals:
        p = prog_by_id.get(g["id"], {})
        current = p.get("current")
        pct = p.get("pct")
        pct_str = f" ({pct:.0f}%)" if pct is not None else ""
        current_str = f" — current: {current} {g.get('unit') or ''}" if current is not None else ""
        safe_desc = sanitize_for_prompt(g["description"], max_len=150)
        lines.append(f"  - {safe_desc}{current_str}{pct_str}")

    return "\n".join(lines)


def memories_as_context(limit: int = 15) -> str:
    """Recent coach memories formatted (and sanitized) for the system prompt."""
    memories = get_recent_memories(limit)
    if not memories:
        return ""

    lines = ["## Coach memory (from previous conversations)"]
    for m in memories:
        date = (m.get("created_at") or "")[:10]
        safe_summary = sanitize_for_prompt(m["summary"], max_len=MEMORY_SUMMARY_MAX_LEN)
        lines.append(f"  - [{date}] {safe_summary}")
    return "\n".join(lines)
