"""AI provider abstraction — Gemini and Claude with a unified interface."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterator

from config import AI_PROVIDER, AI_MODEL, GEMINI_API_KEY, ANTHROPIC_API_KEY


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class ChatResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


# ── Gemini ────────────────────────────────────────────────────────────────────

class GeminiChatSession:
    def __init__(self, system: str, tools: list[dict] | None = None):
        from google import genai
        from google.genai import types

        self._types = types
        client = genai.Client(api_key=GEMINI_API_KEY)
        tool_obj = types.Tool(function_declarations=tools) if tools else None
        self._chat = client.chats.create(
            model=AI_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=[tool_obj] if tool_obj else None,
                temperature=0.7,
            ),
        )

    def send(self, user_message: str) -> ChatResponse:
        return self._parse(self._chat.send_message(user_message))

    def submit_tool_result(self, tool_call: ToolCall, result: dict) -> ChatResponse:
        part = self._types.Part.from_function_response(name=tool_call.name, response=result)
        return self._parse(self._chat.send_message(part))

    def _parse(self, response) -> ChatResponse:
        texts, tool_calls = [], []
        parts = response.candidates[0].content.parts if response.candidates else []
        for part in parts:
            if part.text:
                texts.append(part.text)
            if hasattr(part, "function_call") and part.function_call:
                tool_calls.append(ToolCall(
                    id=part.function_call.name,
                    name=part.function_call.name,
                    args=dict(part.function_call.args),
                ))
        return ChatResponse(text="".join(texts) or None, tool_calls=tool_calls)


# ── Claude ────────────────────────────────────────────────────────────────────

class ClaudeChatSession:
    def __init__(self, system: str, tools: list[dict] | None = None):
        import anthropic
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self._system = system
        self._tools = [
            {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
            for t in (tools or [])
        ]
        self._messages: list[dict] = []

    def send(self, user_message: str) -> ChatResponse:
        self._messages.append({"role": "user", "content": user_message})
        return self._call()

    def submit_tool_result(self, tool_call: ToolCall, result: dict) -> ChatResponse:
        self._messages.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_call.id, "content": json.dumps(result)}],
        })
        return self._call()

    def _call(self) -> ChatResponse:
        kwargs: dict = dict(model=AI_MODEL, system=self._system, messages=self._messages, max_tokens=4096)
        if self._tools:
            kwargs["tools"] = self._tools
        response = self._client.messages.create(**kwargs)
        self._messages.append({"role": "assistant", "content": response.content})
        return self._parse(response)

    def _parse(self, response) -> ChatResponse:
        texts, tool_calls = [], []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, args=block.input))
        return ChatResponse(text="".join(texts) or None, tool_calls=tool_calls)


# ── factory + one-shot streaming ──────────────────────────────────────────────

def create_chat_session(system: str, tools: list[dict] | None = None) -> GeminiChatSession | ClaudeChatSession:
    if AI_PROVIDER == "claude":
        return ClaudeChatSession(system=system, tools=tools)
    return GeminiChatSession(system=system, tools=tools)


def stream_complete(prompt: str, system: str) -> Iterator[str]:
    """One-shot streaming completion with no session state or tool calls."""
    if AI_PROVIDER == "claude":
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        with client.messages.stream(
            model=AI_MODEL,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
        ) as stream:
            yield from stream.text_stream
    else:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        for chunk in client.models.generate_content_stream(
            model=AI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.4),
        ):
            if chunk.text:
                yield chunk.text


def provider_label() -> str:
    return f"{AI_PROVIDER.capitalize()} ({AI_MODEL})"
