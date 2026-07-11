"""End-of-chat memory extraction: distill durable coaching facts from the
transcript (chunked for long sessions), consolidate near-duplicates, and
persist them via db.memories."""

import json

from ai.prompts import (
    _EXTRACT_CHUNK_CHARS,
    _EXTRACT_MAX_CHUNKS,
    _MEMORY_BUDGET_MULTI,
    _MEMORY_BUDGET_SINGLE,
    _MEMORY_CONSOLIDATE_PROMPT,
    _MEMORY_CONSOLIDATE_SYSTEM,
    _MEMORY_PROMPT,
    _MEMORY_SYSTEM,
)
from ai.provider import stream_complete
from ai.sanitize import sanitize_for_prompt


def _split_transcript(messages: list[dict], chunk_chars: int = _EXTRACT_CHUNK_CHARS) -> list[str]:
    """Pack 'ROLE: content' lines into chunks without splitting a message.

    A single message longer than chunk_chars becomes its own chunk, truncated.
    """
    chunks: list[str] = []
    buffer = ""
    for m in messages:
        if not isinstance(m.get("content"), str):
            continue
        line = f"{m['role'].upper()}: {m['content']}"
        if len(line) > chunk_chars:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.append(line[:chunk_chars] + "…")
        elif buffer and len(buffer) + 1 + len(line) > chunk_chars:
            chunks.append(buffer)
            buffer = line
        else:
            buffer = f"{buffer}\n{line}" if buffer else line
    if buffer:
        chunks.append(buffer)
    return chunks


def _parse_memory_json(raw: str) -> list[str]:
    """Parse a model response into a list of usable memory strings."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    items = json.loads(raw)
    if not isinstance(items, list):
        return []
    return [m.strip() for m in items if isinstance(m, str) and len(m.strip()) > 15]


def _extract_from_chunk(chunk_text: str) -> list[str]:
    """One extraction call over one transcript chunk. Never raises."""
    from debug_log import log

    try:
        full_text = "".join(stream_complete(_MEMORY_PROMPT + chunk_text, system=_MEMORY_SYSTEM, max_tokens=1024))
        return _parse_memory_json(full_text)
    except Exception as e:
        log("AI", "Memory chunk extraction failed", error=type(e).__name__)
        return []


def _consolidate_memories(items: list[str], budget: int) -> list[str]:
    """One AI call merging near-duplicates and picking the top `budget` items.

    Falls back to items[:budget] on any failure — a flaky consolidation call
    must never cost the session its memories.
    """
    from debug_log import log

    try:
        prompt = _MEMORY_CONSOLIDATE_PROMPT.format(
            budget=budget,
            candidates="\n".join(f"- {item}" for item in items),
        )
        full_text = "".join(stream_complete(prompt, system=_MEMORY_CONSOLIDATE_SYSTEM, max_tokens=1024))
        merged = _parse_memory_json(full_text)
        if merged:
            return merged[:budget]
    except Exception as e:
        log("AI", "Memory consolidation failed", error=type(e).__name__)
    return items[:budget]


def _extract_and_save_memories(conversation_log: list[dict]) -> int:
    """Extract key facts from the full conversation (chunked) and persist them."""
    from debug_log import log

    if len(conversation_log) < 2:
        return 0
    text_messages = [m for m in conversation_log if isinstance(m.get("content"), str)]
    if len(text_messages) < 2:
        return 0

    chunks = _split_transcript(text_messages)
    if sum(len(c) for c in chunks) < 150:
        return 0
    if len(chunks) > _EXTRACT_MAX_CHUNKS:
        # keep the opening chunk (goals/injuries are stated up front) plus the
        # most recent ones — the end is what the old head-slice used to lose
        log("AI", "Transcript capped for extraction", chunks=len(chunks), kept=_EXTRACT_MAX_CHUNKS)
        chunks = chunks[:1] + chunks[-(_EXTRACT_MAX_CHUNKS - 1) :]

    items: list[str] = []
    for i, chunk in enumerate(chunks):
        got = _extract_from_chunk(chunk)
        log("AI", "Memory chunk extracted", chunk=i + 1, of=len(chunks), items=len(got))
        items += got

    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        key = " ".join(item.split()).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    if len(chunks) == 1:
        # classic path: one extraction call, plain cap — no consolidation cost
        deduped = deduped[:_MEMORY_BUDGET_SINGLE]
    elif len(deduped) > _MEMORY_BUDGET_MULTI:
        deduped = _consolidate_memories(deduped, _MEMORY_BUDGET_MULTI)

    saved = 0
    try:
        from db.memories import MEMORY_SUMMARY_MAX_LEN, save_memory

        for mem in deduped:
            # Sanitize before storing — prevents injected text from
            # persisting as a "memory" across future sessions.
            clean = sanitize_for_prompt(mem.strip(), max_len=MEMORY_SUMMARY_MAX_LEN)
            if clean:
                save_memory(clean)
                saved += 1
    except Exception as e:
        log("AI", "Memory save failed", error=type(e).__name__, saved=saved)
    log("AI", "Memories saved", chunks=len(chunks), merged=len(deduped), saved=saved)
    return saved


def _tool_action_log_entry(name: str, args: dict, result: dict) -> str | None:
    """Compact synthetic transcript line for a tool call, or None if not log-worthy.

    Declines are logged deliberately — a rejected routine or goal change is a
    strong preference signal for the memory extractor.
    """
    if not isinstance(result, dict) or "error" in result:
        return None

    declined = result.get("success") is False and result.get("message") == "User declined"

    if name in ("push_routine", "update_routine"):
        title = sanitize_for_prompt(str(args.get("title") or ""), max_len=100) or "untitled"
        n = len(args.get("exercises") or [])
        if declined:
            verb = "routine" if name == "push_routine" else "update to routine"
            return f"[action] User declined the proposed {verb} '{title}'."
        if result.get("success"):
            if name == "push_routine":
                return f"[action] Pushed new routine '{title}' ({n} exercises) to Hevy."
            return f"[action] Updated routine '{title}' ({n} exercises)."
        return None

    if name == "manage_goals":
        summary = sanitize_for_prompt(str(args.get("changes_summary") or ""), max_len=200)
        if declined:
            return f"[action] User declined goal change: {summary}" if summary else None
        if result.get("success") and summary:
            return f"[action] Goal {result.get('action', 'changed')}: {summary}"
        return None

    return None  # find_exercises and anything else: read-only, not a decision
