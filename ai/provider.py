"""AI provider abstraction — Gemini, Claude, OpenRouter, Groq, GitHub Models, Bedrock."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterator

from config import (
    AI_PROVIDER, AI_MODEL,
    GEMINI_API_KEY, ANTHROPIC_API_KEY,
    OPENROUTER_API_KEY, GROQ_API_KEY, GITHUB_TOKEN,
    AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN,
    PROVIDER_BASE_URLS,
    get_provider_api_key,
)


def _track_usage(input_tokens: int = 0, output_tokens: int = 0, cache_read: int = 0) -> None:
    """Persist token usage totals silently — never raises."""
    if not (input_tokens or output_tokens or cache_read):
        return
    try:
        from db.goals import add_token_usage
        add_token_usage(input_tokens, output_tokens, cache_read)
    except Exception:
        pass


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
        self._client = genai.Client(api_key=GEMINI_API_KEY)
        tool_obj = types.Tool(function_declarations=tools) if tools else None
        self._chat = self._client.chats.create(
            model=AI_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=[tool_obj] if tool_obj else None,
                temperature=0.7,
            ),
        )

    def send(self, user_message: str) -> ChatResponse:
        return self._parse(self._chat.send_message(user_message))

    def discard_pending_user(self) -> None:
        pass  # Gemini SDK owns history; rollback not supported

    def submit_tool_result(self, tool_call: ToolCall, result: dict) -> ChatResponse:
        return self.submit_tool_results([(tool_call, result)])

    def submit_tool_results(self, results: list[tuple[ToolCall, dict]]) -> ChatResponse:
        parts = [
            self._types.Part.from_function_response(name=tc.name, response=r)
            for tc, r in results
        ]
        return self._parse(self._chat.send_message(parts))

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
        um = getattr(response, "usage_metadata", None)
        if um:
            _track_usage(
                getattr(um, "prompt_token_count", 0) or 0,
                getattr(um, "candidates_token_count", 0) or 0,
            )
        return ChatResponse(text="".join(texts) or None, tool_calls=tool_calls)


# ── Claude (Anthropic direct) ─────────────────────────────────────────────────

class ClaudeChatSession:
    def __init__(self, system: str, tools: list[dict] | None = None):
        import anthropic
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self._system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        self._tools = [
            {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
            for t in (tools or [])
        ]
        self._messages: list[dict] = []

    def send(self, user_message: str) -> ChatResponse:
        self._messages.append({"role": "user", "content": user_message})
        return self._call()

    def discard_pending_user(self) -> None:
        if self._messages and self._messages[-1].get("role") != "assistant":
            self._messages.pop()

    def submit_tool_result(self, tool_call: ToolCall, result: dict) -> ChatResponse:
        return self.submit_tool_results([(tool_call, result)])

    def submit_tool_results(self, results: list[tuple[ToolCall, dict]]) -> ChatResponse:
        self._messages.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tc.id, "content": json.dumps(r)}
                for tc, r in results
            ],
        })
        return self._call()

    def _call(self) -> ChatResponse:
        kwargs: dict = dict(model=AI_MODEL, system=self._system, messages=self._messages, max_tokens=4096)
        if self._tools:
            kwargs["tools"] = self._tools
        response = self._client.messages.create(**kwargs)
        self._messages.append({"role": "assistant", "content": response.content})
        _track_usage(
            response.usage.input_tokens,
            response.usage.output_tokens,
            getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        )
        return self._parse(response)

    def _parse(self, response) -> ChatResponse:
        texts, tool_calls = [], []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, args=block.input))
        return ChatResponse(text="".join(texts) or None, tool_calls=tool_calls)


# ── OpenAI-compatible (OpenRouter / Groq / GitHub Models) ────────────────────

class OpenAICompatibleChatSession:
    """Works with any provider that speaks the OpenAI Chat Completions API."""

    def __init__(self, system: str, tools: list[dict] | None = None):
        from openai import OpenAI
        base_url = PROVIDER_BASE_URLS[AI_PROVIDER]
        api_key = get_provider_api_key()
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self._tools = [
            {"type": "function", "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            }}
            for t in (tools or [])
        ]
        self._messages: list[dict] = [{"role": "system", "content": system}]

    def send(self, user_message: str) -> ChatResponse:
        self._messages.append({"role": "user", "content": user_message})
        return self._call()

    def discard_pending_user(self) -> None:
        if self._messages and self._messages[-1].get("role") not in ("assistant", "system"):
            self._messages.pop()

    def submit_tool_result(self, tool_call: ToolCall, result: dict) -> ChatResponse:
        return self.submit_tool_results([(tool_call, result)])

    def submit_tool_results(self, results: list[tuple[ToolCall, dict]]) -> ChatResponse:
        for tc, r in results:
            self._messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(r)})
        return self._call()

    def _call(self) -> ChatResponse:
        kwargs: dict = dict(model=AI_MODEL, messages=self._messages, max_tokens=4096)
        if self._tools:
            kwargs["tools"] = self._tools
        response = self._client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        # Persist assistant turn (including any tool_calls) for next round
        msg_dict: dict = {"role": "assistant"}
        if msg.content:
            msg_dict["content"] = msg.content
        if msg.tool_calls:
            msg_dict["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
        self._messages.append(msg_dict)
        if response.usage:
            _track_usage(response.usage.prompt_tokens, response.usage.completion_tokens)
        return self._parse(msg)

    def _parse(self, msg) -> ChatResponse:
        texts, tool_calls = [], []
        if msg.content:
            texts.append(msg.content)
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, args=args))
        return ChatResponse(text="".join(texts) or None, tool_calls=tool_calls)


# ── Amazon Bedrock (Claude via Anthropic SDK) ─────────────────────────────────

class BedrockChatSession:
    """Claude-on-Bedrock via anthropic.AnthropicBedrock. Requires: pip install 'anthropic[bedrock]'."""

    def __init__(self, system: str, tools: list[dict] | None = None):
        import anthropic
        try:
            self._client = anthropic.AnthropicBedrock(
                aws_region=AWS_REGION,
                aws_access_key=AWS_ACCESS_KEY_ID or None,
                aws_secret_key=AWS_SECRET_ACCESS_KEY or None,
                aws_session_token=AWS_SESSION_TOKEN or None,
            )
        except Exception as exc:
            if "botocore" in str(exc) or "boto3" in str(exc):
                raise RuntimeError(
                    'Bedrock requires the AWS SDK extras.\n'
                    'Run: pip install "anthropic[bedrock]"'
                ) from exc
            raise
        self._system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        self._tools = [
            {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
            for t in (tools or [])
        ]
        self._messages: list[dict] = []

    def send(self, user_message: str) -> ChatResponse:
        self._messages.append({"role": "user", "content": user_message})
        return self._call()

    def discard_pending_user(self) -> None:
        if self._messages and self._messages[-1].get("role") != "assistant":
            self._messages.pop()

    def submit_tool_result(self, tool_call: ToolCall, result: dict) -> ChatResponse:
        return self.submit_tool_results([(tool_call, result)])

    def submit_tool_results(self, results: list[tuple[ToolCall, dict]]) -> ChatResponse:
        self._messages.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tc.id, "content": json.dumps(r)}
                for tc, r in results
            ],
        })
        return self._call()

    def _call(self) -> ChatResponse:
        kwargs: dict = dict(model=AI_MODEL, system=self._system, messages=self._messages, max_tokens=4096)
        if self._tools:
            kwargs["tools"] = self._tools
        response = self._client.messages.create(**kwargs)
        self._messages.append({"role": "assistant", "content": response.content})
        _track_usage(
            response.usage.input_tokens,
            response.usage.output_tokens,
            getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        )
        texts, tool_calls = [], []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, args=block.input))
        return ChatResponse(text="".join(texts) or None, tool_calls=tool_calls)


# ── Amazon Bedrock (non-Claude models via boto3 Converse API) ─────────────────

def _boto3_bedrock_client():
    import boto3
    kwargs: dict = {"region_name": AWS_REGION}
    if AWS_ACCESS_KEY_ID:
        kwargs["aws_access_key_id"] = AWS_ACCESS_KEY_ID
    if AWS_SECRET_ACCESS_KEY:
        kwargs["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY
    if AWS_SESSION_TOKEN:
        kwargs["aws_session_token"] = AWS_SESSION_TOKEN
    return boto3.client("bedrock-runtime", **kwargs)


class BedrockConverseChatSession:
    """Non-Claude models on Bedrock (Gemma, Llama, etc.) via boto3 Converse API."""

    def __init__(self, system: str, tools: list[dict] | None = None):
        self._client = _boto3_bedrock_client()
        self._system = [{"text": system}]
        self._tool_config: dict | None = None
        if tools:
            self._tool_config = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": t["name"],
                            "description": t["description"],
                            "inputSchema": {"json": t["parameters"]},
                        }
                    }
                    for t in tools
                ]
            }
        self._messages: list[dict] = []

    def send(self, user_message: str) -> ChatResponse:
        self._messages.append({"role": "user", "content": [{"text": user_message}]})
        return self._call()

    def discard_pending_user(self) -> None:
        if self._messages and self._messages[-1].get("role") != "assistant":
            self._messages.pop()

    def submit_tool_result(self, tool_call: ToolCall, result: dict) -> ChatResponse:
        return self.submit_tool_results([(tool_call, result)])

    def submit_tool_results(self, results: list[tuple[ToolCall, dict]]) -> ChatResponse:
        self._messages.append({
            "role": "user",
            "content": [
                {"toolResult": {"toolUseId": tc.id, "content": [{"text": json.dumps(r)}]}}
                for tc, r in results
            ],
        })
        return self._call()

    def _call(self) -> ChatResponse:
        kwargs: dict = {
            "modelId": AI_MODEL,
            "system": self._system,
            "messages": self._messages,
            "inferenceConfig": {"maxTokens": 4096},
        }
        if self._tool_config:
            kwargs["toolConfig"] = self._tool_config
        response = self._client.converse(**kwargs)
        msg = response["output"]["message"]
        self._messages.append(msg)
        usage = response.get("usage", {})
        _track_usage(usage.get("inputTokens", 0), usage.get("outputTokens", 0))
        texts, tool_calls = [], []
        for item in msg.get("content", []):
            if "text" in item:
                texts.append(item["text"])
            elif "toolUse" in item:
                tu = item["toolUse"]
                tool_calls.append(ToolCall(id=tu["toolUseId"], name=tu["name"], args=tu["input"]))
        return ChatResponse(text="".join(texts) or None, tool_calls=tool_calls)


# ── factory + one-shot streaming ──────────────────────────────────────────────

_OPENAI_COMPAT = {"openrouter", "groq", "github"}


def create_chat_session(system: str, tools: list[dict] | None = None):
    if AI_PROVIDER == "claude":
        return ClaudeChatSession(system=system, tools=tools)
    if AI_PROVIDER in _OPENAI_COMPAT:
        return OpenAICompatibleChatSession(system=system, tools=tools)
    if AI_PROVIDER == "bedrock":
        if AI_MODEL.startswith("anthropic."):
            return BedrockChatSession(system=system, tools=tools)
        return BedrockConverseChatSession(system=system, tools=tools)
    return GeminiChatSession(system=system, tools=tools)


def stream_complete(prompt: str, system: str) -> Iterator[str]:
    """One-shot streaming completion with no session state or tool calls."""
    if AI_PROVIDER == "claude":
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        cached_system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        with client.messages.stream(
            model=AI_MODEL, system=cached_system,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
        ) as stream:
            yield from stream.text_stream
            final = stream.get_final_message()
            _track_usage(
                final.usage.input_tokens,
                final.usage.output_tokens,
                getattr(final.usage, "cache_read_input_tokens", 0) or 0,
            )

    elif AI_PROVIDER in _OPENAI_COMPAT:
        from openai import OpenAI
        client = OpenAI(base_url=PROVIDER_BASE_URLS[AI_PROVIDER], api_key=get_provider_api_key())
        stream = client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=4096,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    elif AI_PROVIDER == "bedrock":
        if AI_MODEL.startswith("anthropic."):
            import anthropic
            try:
                client = anthropic.AnthropicBedrock(
                    aws_region=AWS_REGION,
                    aws_access_key=AWS_ACCESS_KEY_ID or None,
                    aws_secret_key=AWS_SECRET_ACCESS_KEY or None,
                    aws_session_token=AWS_SESSION_TOKEN or None,
                )
            except Exception as exc:
                if "botocore" in str(exc) or "boto3" in str(exc):
                    raise RuntimeError(
                        'Bedrock requires the AWS SDK extras.\n'
                        'Run: pip install "anthropic[bedrock]"'
                    ) from exc
                raise
            cached_system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            with client.messages.stream(
                model=AI_MODEL, system=cached_system,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
            ) as stream:
                yield from stream.text_stream
                final = stream.get_final_message()
                _track_usage(
                    final.usage.input_tokens,
                    final.usage.output_tokens,
                    getattr(final.usage, "cache_read_input_tokens", 0) or 0,
                )
        else:
            client = _boto3_bedrock_client()
            response = client.converse_stream(
                modelId=AI_MODEL,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 4096},
            )
            for event in response.get("stream", []):
                if "contentBlockDelta" in event:
                    delta = event["contentBlockDelta"].get("delta", {})
                    if "text" in delta:
                        yield delta["text"]

    else:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        for chunk in client.models.generate_content_stream(
            model=AI_MODEL, contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.4),
        ):
            if chunk.text:
                yield chunk.text


def provider_label() -> str:
    labels = {
        "openrouter": "OpenRouter",
        "groq":       "Groq",
        "github":     "GitHub Models",
        "bedrock":    "Amazon Bedrock",
        "claude":     "Claude",
        "gemini":     "Gemini",
    }
    name = labels.get(AI_PROVIDER, AI_PROVIDER.capitalize())
    return f"{name} ({AI_MODEL})"
