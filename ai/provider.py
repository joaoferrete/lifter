"""AI provider abstraction — Gemini, Claude, OpenRouter, Groq, GitHub Models, Bedrock."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from config import (
    AI_MODEL,
    AI_PROVIDER,
    ANTHROPIC_API_KEY,
    AWS_ACCESS_KEY_ID,
    AWS_BEARER_TOKEN_BEDROCK,
    AWS_REGION,
    AWS_SECRET_ACCESS_KEY,
    AWS_SESSION_TOKEN,
    GEMINI_API_KEY,
    KNOWN_PROVIDERS,
    PROVIDER_BASE_URLS,
    get_provider_api_key,
)

# Default output-token ceiling. Per-task callers pass smaller values (e.g. memory
# extraction, short JSON) so we don't reserve a large budget for tiny outputs.
_DEFAULT_MAX_TOKENS = 4096

# How many recent user turns a chat keeps verbatim before older ones get folded
# into a running summary. 0 disables windowing (unlimited history).
_DEFAULT_HISTORY_TURNS = 12


def _history_keep_turns() -> int:
    """Configured number of recent user turns to keep verbatim (pref, default 12)."""
    try:
        from db.goals import get_pref

        raw = get_pref("ai_chat_history_turns")
        return max(0, int(raw)) if raw is not None else _DEFAULT_HISTORY_TURNS
    except Exception:
        return _DEFAULT_HISTORY_TURNS


_SUMMARY_SYSTEM = (
    "You compress chat history for a fitness coach. Output a terse summary (2-5 sentences) "
    "capturing durable facts about the athlete, decisions made, and the session's goal. "
    "No preamble, no markdown — just the summary text."
)


def _summarize_history(old_summary: str, dropped_texts: list[str]) -> str:
    """Fold dropped chat turns into a short running summary. Best-effort.

    Returns the previous summary unchanged on any failure, so windowing never
    breaks a live conversation."""
    joined = "\n".join(t for t in dropped_texts if t)[:4000]
    if not joined.strip():
        return old_summary
    prompt = (
        f"Existing summary (may be empty):\n{old_summary or '(none)'}\n\n"
        f"Older messages to fold in:\n{joined}\n\n"
        "Return the updated summary."
    )
    try:
        out = "".join(stream_complete(prompt, system=_SUMMARY_SYSTEM, max_tokens=256)).strip()
        return out or old_summary
    except Exception:
        return old_summary


class _WindowedChat:
    """Mixin: trims old turns from a message-list chat session, folding them into
    a running summary stored in the system block.

    Subclasses set ``self._keep_turns`` and ``self._summary`` and implement the
    small format-specific adapters below. Cutting only at real user-utterance
    boundaries guarantees we never split a tool_use / tool_result pair."""

    # ── adapters (overridden per provider) ────────────────────────────────────
    def _history_messages(self) -> list:
        raise NotImplementedError

    def _set_history_messages(self, msgs: list) -> None:
        raise NotImplementedError

    def _is_user_utterance(self, msg) -> bool:
        raise NotImplementedError

    def _msg_text(self, msg) -> str:
        raise NotImplementedError

    def _set_summary_in_system(self, summary: str) -> None:
        raise NotImplementedError

    # ── orchestration ─────────────────────────────────────────────────────────
    def _maybe_window(self) -> None:
        keep = getattr(self, "_keep_turns", 0)
        if not keep:
            return
        msgs = self._history_messages()
        utterances = [i for i, m in enumerate(msgs) if self._is_user_utterance(m)]
        if len(utterances) <= keep:
            return
        cut = utterances[len(utterances) - keep]
        dropped, kept = msgs[:cut], msgs[cut:]
        texts = [self._msg_text(m) for m in dropped]
        self._summary = _summarize_history(getattr(self, "_summary", ""), texts)
        if self._summary:
            self._set_summary_in_system(self._summary)
        self._set_history_messages(kept)
        try:
            from debug_log import log

            log("AI", "chat history windowed", keep_turns=keep, dropped_msgs=len(dropped), kept_msgs=len(kept))
        except Exception:
            pass


def _blocks_text(content) -> str:
    """Best-effort text extraction from a string / list-of-blocks message body."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for it in content:
            if isinstance(it, dict):
                if (it.get("type") == "text" and it.get("text")) or ("text" in it and isinstance(it["text"], str)):
                    out.append(it["text"])
            elif getattr(it, "type", None) == "text":
                out.append(getattr(it, "text", "") or "")
        return " ".join(out)
    return ""


def _track_usage(input_tokens: int = 0, output_tokens: int = 0, cache_read: int = 0) -> None:
    """Persist token usage totals silently — never raises."""
    if not (input_tokens or output_tokens or cache_read):
        return
    try:
        from db.goals import add_token_usage

        add_token_usage(input_tokens, output_tokens, cache_read)
    except Exception:
        pass
    try:
        import config as _cfg
        from debug_log import log

        log(
            "AI",
            "Token usage",
            provider=_cfg.AI_PROVIDER,
            model=_cfg.AI_MODEL,
            input=input_tokens,
            output=output_tokens,
            cache_read=cache_read,
        )
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
    # Normalized across providers: "max_tokens" | "end" | "tool_use" | raw | None.
    stop_reason: str | None = None


def _norm_stop_reason(raw) -> str | None:
    """Map provider-specific stop/finish reasons onto a common vocabulary.

    Uses str() + substring matching so SDK enums (e.g. Gemini's
    FinishReason.MAX_TOKENS) and plain strings both normalize safely.
    """
    if raw is None:
        return None
    s = str(raw).lower()
    if "max_tokens" in s or s.endswith("length"):
        return "max_tokens"
    if "tool" in s:
        return "tool_use"
    if "stop" in s or "end" in s:
        return "end"
    return s


# ── Gemini ────────────────────────────────────────────────────────────────────


class GeminiChatSession:
    def __init__(self, system: str, tools: list[dict] | None = None, max_tokens: int = _DEFAULT_MAX_TOKENS):
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=GEMINI_API_KEY)
        # Kept so we can rebuild the chat with a trimmed history during windowing.
        self._base_system = system
        self._max_tokens = max_tokens
        self._keep_turns = _history_keep_turns()
        self._summary = ""
        # The genai SDK validates plain-dict declarations at runtime.
        self._tool_obj = types.Tool(function_declarations=tools) if tools else None  # type: ignore[arg-type]
        self._chat = self._new_chat(system, [])

    def _new_chat(self, system: str, history: list):
        types = self._types
        return self._client.chats.create(
            model=AI_MODEL,
            history=history,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=[self._tool_obj] if self._tool_obj else None,
                temperature=0.7,
                max_output_tokens=self._max_tokens,
            ),
        )

    def _maybe_window(self) -> None:
        """Best-effort: fold old turns into a summary and rebuild the chat with a
        trimmed history. The Gemini SDK owns history, so this reads get_history()
        and recreates the chat — guarded so any SDK-shape mismatch leaves the live
        chat untouched (degrades to unlimited history)."""
        if not self._keep_turns:
            return
        try:
            history = list(self._chat.get_history())
        except Exception:
            return  # SDK doesn't expose history in the expected way — skip safely

        def _is_user_utterance(c) -> bool:
            if getattr(c, "role", None) != "user":
                return False
            parts = getattr(c, "parts", None) or []
            return not any(getattr(p, "function_response", None) for p in parts)

        def _text(c) -> str:
            return " ".join(getattr(p, "text", "") or "" for p in (getattr(c, "parts", None) or []))

        utterances = [i for i, c in enumerate(history) if _is_user_utterance(c)]
        if len(utterances) <= self._keep_turns:
            return
        cut = utterances[len(utterances) - self._keep_turns]
        dropped, kept = history[:cut], history[cut:]
        self._summary = _summarize_history(self._summary, [_text(c) for c in dropped])
        new_system = self._base_system
        if self._summary:
            new_system = f"{self._base_system}\n\n## Summary of earlier conversation\n{self._summary}"
        try:
            self._chat = self._new_chat(new_system, kept)
        except Exception:
            return  # keep the existing chat if rebuild fails
        try:
            from debug_log import log

            log(
                "AI",
                "chat history windowed",
                keep_turns=self._keep_turns,
                dropped_msgs=len(dropped),
                kept_msgs=len(kept),
            )
        except Exception:
            pass

    def send(self, user_message: str) -> ChatResponse:
        result = self._parse(self._chat.send_message(user_message))
        self._maybe_window()
        return result

    def discard_pending_user(self) -> None:
        pass  # Gemini SDK owns history; rollback not supported

    def submit_tool_result(self, tool_call: ToolCall, result: dict) -> ChatResponse:
        return self.submit_tool_results([(tool_call, result)])

    def submit_tool_results(self, results: list[tuple[ToolCall, dict]]) -> ChatResponse:
        parts = [self._types.Part.from_function_response(name=tc.name, response=r) for tc, r in results]
        return self._parse(self._chat.send_message(parts))

    def _parse(self, response) -> ChatResponse:
        texts, tool_calls = [], []
        parts = response.candidates[0].content.parts if response.candidates else []
        for part in parts:
            if part.text:
                texts.append(part.text)
            if hasattr(part, "function_call") and part.function_call:
                tool_calls.append(
                    ToolCall(
                        id=part.function_call.name,
                        name=part.function_call.name,
                        args=dict(part.function_call.args),
                    )
                )
        um = getattr(response, "usage_metadata", None)
        if um:
            _track_usage(
                getattr(um, "prompt_token_count", 0) or 0,
                getattr(um, "candidates_token_count", 0) or 0,
            )
        finish = getattr(response.candidates[0], "finish_reason", None) if response.candidates else None
        return ChatResponse(text="".join(texts) or None, tool_calls=tool_calls, stop_reason=_norm_stop_reason(finish))


# ── Claude (Anthropic direct) ─────────────────────────────────────────────────


class ClaudeChatSession(_WindowedChat):
    def __init__(self, system: str, tools: list[dict] | None = None, max_tokens: int = _DEFAULT_MAX_TOKENS):
        import anthropic

        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self._max_tokens = max_tokens
        self._base_system = system
        self._keep_turns = _history_keep_turns()
        self._summary = ""
        self._system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        self._tools = [
            {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in (tools or [])
        ]
        self._messages: list[dict] = []

    # ── windowing adapters ────────────────────────────────────────────────────
    def _history_messages(self) -> list:
        return self._messages

    def _set_history_messages(self, msgs: list) -> None:
        self._messages = msgs

    def _is_user_utterance(self, msg) -> bool:
        return msg.get("role") == "user" and isinstance(msg.get("content"), str)

    def _msg_text(self, msg) -> str:
        return _blocks_text(msg.get("content"))

    def _set_summary_in_system(self, summary: str) -> None:
        text = f"{self._base_system}\n\n## Summary of earlier conversation\n{summary}"
        self._system = [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    def send(self, user_message: str) -> ChatResponse:
        self._messages.append({"role": "user", "content": user_message})
        return self._call()

    def discard_pending_user(self) -> None:
        if self._messages and self._messages[-1].get("role") != "assistant":
            self._messages.pop()

    def submit_tool_result(self, tool_call: ToolCall, result: dict) -> ChatResponse:
        return self.submit_tool_results([(tool_call, result)])

    def submit_tool_results(self, results: list[tuple[ToolCall, dict]]) -> ChatResponse:
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tc.id, "content": json.dumps(r)} for tc, r in results
                ],
            }
        )
        return self._call()

    def _call(self) -> ChatResponse:
        kwargs: dict = {
            "model": AI_MODEL,
            "system": self._system,
            "messages": self._messages,
            "max_tokens": self._max_tokens,
        }
        if self._tools:
            kwargs["tools"] = self._tools
        response = self._client.messages.create(**kwargs)
        self._messages.append({"role": "assistant", "content": response.content})
        _track_usage(
            response.usage.input_tokens,
            response.usage.output_tokens,
            getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        )
        self._maybe_window()
        return self._parse(response)

    def _parse(self, response) -> ChatResponse:
        texts, tool_calls = [], []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, args=block.input))
        return ChatResponse(
            text="".join(texts) or None,
            tool_calls=tool_calls,
            stop_reason=_norm_stop_reason(getattr(response, "stop_reason", None)),
        )


# ── OpenAI-compatible (OpenRouter / Groq / GitHub Models) ────────────────────


class OpenAICompatibleChatSession(_WindowedChat):
    """Works with any provider that speaks the OpenAI Chat Completions API."""

    def __init__(self, system: str, tools: list[dict] | None = None, max_tokens: int = _DEFAULT_MAX_TOKENS):
        from openai import OpenAI

        base_url = PROVIDER_BASE_URLS[AI_PROVIDER]
        api_key = get_provider_api_key()
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self._max_tokens = max_tokens
        self._base_system = system
        self._keep_turns = _history_keep_turns()
        self._summary = ""
        self._tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in (tools or [])
        ]
        self._messages: list[dict] = [{"role": "system", "content": system}]

    # ── windowing adapters (messages[0] is the system message — keep it) ──────
    def _history_messages(self) -> list:
        return self._messages[1:]

    def _set_history_messages(self, msgs: list) -> None:
        self._messages = self._messages[:1] + msgs

    def _is_user_utterance(self, msg) -> bool:
        return msg.get("role") == "user" and isinstance(msg.get("content"), str)

    def _msg_text(self, msg) -> str:
        return _blocks_text(msg.get("content"))

    def _set_summary_in_system(self, summary: str) -> None:
        self._messages[0] = {
            "role": "system",
            "content": f"{self._base_system}\n\n## Summary of earlier conversation\n{summary}",
        }

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
        kwargs: dict = {"model": AI_MODEL, "messages": self._messages, "max_tokens": self._max_tokens}
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
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        self._messages.append(msg_dict)
        if response.usage:
            _track_usage(response.usage.prompt_tokens, response.usage.completion_tokens)
        self._maybe_window()
        return self._parse(msg, getattr(response.choices[0], "finish_reason", None))

    def _parse(self, msg, finish_reason=None) -> ChatResponse:
        texts, tool_calls = [], []
        if msg.content:
            texts.append(msg.content)
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, args=args))
        return ChatResponse(
            text="".join(texts) or None, tool_calls=tool_calls, stop_reason=_norm_stop_reason(finish_reason)
        )


# ── Amazon Bedrock (Claude via Anthropic SDK) ─────────────────────────────────


def _anthropic_bedrock_client():
    """Build an AnthropicBedrock client for Claude-on-Bedrock.

    Two auth paths:
      • Bearer token (AWS_BEARER_TOKEN_BEDROCK) — the SDK sends
        ``Authorization: Bearer …`` and skips SigV4, so boto3/botocore is NOT
        required. Cannot be combined with AWS credentials (the SDK rejects it).
      • AWS credentials via boto3 (needs: pip install 'anthropic[bedrock]').
    """
    import anthropic

    if AWS_BEARER_TOKEN_BEDROCK:
        # Bearer path: pass only the region + api_key — passing any aws_* arg
        # alongside api_key makes the SDK raise ValueError.
        return anthropic.AnthropicBedrock(
            aws_region=AWS_REGION,
            api_key=AWS_BEARER_TOKEN_BEDROCK,
        )
    if not (AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY):
        raise RuntimeError(
            "Amazon Bedrock has no credentials configured.\n"
            "Set AWS_BEARER_TOKEN_BEDROCK (a Bedrock API key / bearer token), or "
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, in your .env."
        )
    try:
        return anthropic.AnthropicBedrock(
            aws_region=AWS_REGION,
            aws_access_key=AWS_ACCESS_KEY_ID or None,
            aws_secret_key=AWS_SECRET_ACCESS_KEY or None,
            aws_session_token=AWS_SESSION_TOKEN or None,
        )
    except Exception as exc:
        if "botocore" in str(exc) or "boto3" in str(exc):
            raise RuntimeError(
                "Bedrock with AWS credentials requires the AWS SDK extras.\n"
                'Run: pip install "anthropic[bedrock]" — or use a bearer token '
                "(AWS_BEARER_TOKEN_BEDROCK), which needs no extra install."
            ) from exc
        raise


class BedrockChatSession(_WindowedChat):
    """Claude-on-Bedrock via anthropic.AnthropicBedrock.

    Auth: a bearer token (AWS_BEARER_TOKEN_BEDROCK, no boto3 needed) or AWS
    credentials (pip install 'anthropic[bedrock]'). See _anthropic_bedrock_client.
    """

    def __init__(self, system: str, tools: list[dict] | None = None, max_tokens: int = _DEFAULT_MAX_TOKENS):
        self._max_tokens = max_tokens
        self._base_system = system
        self._keep_turns = _history_keep_turns()
        self._summary = ""
        self._client = _anthropic_bedrock_client()
        self._system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        self._tools = [
            {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in (tools or [])
        ]
        self._messages: list[dict] = []

    # ── windowing adapters (same message shape as ClaudeChatSession) ──────────
    def _history_messages(self) -> list:
        return self._messages

    def _set_history_messages(self, msgs: list) -> None:
        self._messages = msgs

    def _is_user_utterance(self, msg) -> bool:
        return msg.get("role") == "user" and isinstance(msg.get("content"), str)

    def _msg_text(self, msg) -> str:
        return _blocks_text(msg.get("content"))

    def _set_summary_in_system(self, summary: str) -> None:
        text = f"{self._base_system}\n\n## Summary of earlier conversation\n{summary}"
        self._system = [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    def send(self, user_message: str) -> ChatResponse:
        self._messages.append({"role": "user", "content": user_message})
        return self._call()

    def discard_pending_user(self) -> None:
        if self._messages and self._messages[-1].get("role") != "assistant":
            self._messages.pop()

    def submit_tool_result(self, tool_call: ToolCall, result: dict) -> ChatResponse:
        return self.submit_tool_results([(tool_call, result)])

    def submit_tool_results(self, results: list[tuple[ToolCall, dict]]) -> ChatResponse:
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tc.id, "content": json.dumps(r)} for tc, r in results
                ],
            }
        )
        return self._call()

    def _call(self) -> ChatResponse:
        kwargs: dict = {
            "model": AI_MODEL,
            "system": self._system,
            "messages": self._messages,
            "max_tokens": self._max_tokens,
        }
        if self._tools:
            kwargs["tools"] = self._tools
        response = self._client.messages.create(**kwargs)
        self._messages.append({"role": "assistant", "content": response.content})
        _track_usage(
            response.usage.input_tokens,
            response.usage.output_tokens,
            getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        )
        self._maybe_window()
        texts, tool_calls = [], []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, args=block.input))
        return ChatResponse(
            text="".join(texts) or None,
            tool_calls=tool_calls,
            stop_reason=_norm_stop_reason(getattr(response, "stop_reason", None)),
        )


# ── Amazon Bedrock (non-Claude models via boto3 Converse API) ─────────────────


def _boto3_bedrock_client():
    """boto3 bedrock-runtime client for non-Claude (Converse) models.

    Unlike the Claude path, the Converse API requires boto3/botocore. A bearer
    token (AWS_BEARER_TOKEN_BEDROCK) is honored automatically by recent botocore
    versions — it's read from the environment, so we don't pass it here.
    """
    if not (AWS_BEARER_TOKEN_BEDROCK or (AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY)):
        raise RuntimeError(
            "Amazon Bedrock has no credentials configured.\n"
            "Set AWS_BEARER_TOKEN_BEDROCK (a Bedrock API key / bearer token), or "
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, in your .env."
        )
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            f"Bedrock model '{AI_MODEL}' uses the Converse API, which requires boto3.\n"
            'Run: pip install "anthropic[bedrock]" — or pick a Claude model '
            "(anthropic.*), which works with a bearer token alone."
        ) from exc
    kwargs: dict = {"region_name": AWS_REGION}
    if AWS_ACCESS_KEY_ID:
        kwargs["aws_access_key_id"] = AWS_ACCESS_KEY_ID
    if AWS_SECRET_ACCESS_KEY:
        kwargs["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY
    if AWS_SESSION_TOKEN:
        kwargs["aws_session_token"] = AWS_SESSION_TOKEN
    return boto3.client("bedrock-runtime", **kwargs)


class BedrockConverseChatSession(_WindowedChat):
    """Non-Claude models on Bedrock (Gemma, Llama, etc.) via boto3 Converse API."""

    def __init__(self, system: str, tools: list[dict] | None = None, max_tokens: int = _DEFAULT_MAX_TOKENS):
        self._client = _boto3_bedrock_client()
        self._max_tokens = max_tokens
        self._base_system = system
        self._keep_turns = _history_keep_turns()
        self._summary = ""
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

    # ── windowing adapters (Converse content is a list of typed items) ────────
    def _history_messages(self) -> list:
        return self._messages

    def _set_history_messages(self, msgs: list) -> None:
        self._messages = msgs

    def _is_user_utterance(self, msg) -> bool:
        content = msg.get("content") or []
        return msg.get("role") == "user" and not any(isinstance(it, dict) and "toolResult" in it for it in content)

    def _msg_text(self, msg) -> str:
        return " ".join(
            it["text"] for it in (msg.get("content") or []) if isinstance(it, dict) and isinstance(it.get("text"), str)
        )

    def _set_summary_in_system(self, summary: str) -> None:
        self._system = [{"text": f"{self._base_system}\n\n## Summary of earlier conversation\n{summary}"}]

    def send(self, user_message: str) -> ChatResponse:
        self._messages.append({"role": "user", "content": [{"text": user_message}]})
        return self._call()

    def discard_pending_user(self) -> None:
        if self._messages and self._messages[-1].get("role") != "assistant":
            self._messages.pop()

    def submit_tool_result(self, tool_call: ToolCall, result: dict) -> ChatResponse:
        return self.submit_tool_results([(tool_call, result)])

    def submit_tool_results(self, results: list[tuple[ToolCall, dict]]) -> ChatResponse:
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {"toolResult": {"toolUseId": tc.id, "content": [{"text": json.dumps(r)}]}} for tc, r in results
                ],
            }
        )
        return self._call()

    def _call(self) -> ChatResponse:
        kwargs: dict = {
            "modelId": AI_MODEL,
            "system": self._system,
            "messages": self._messages,
            "inferenceConfig": {"maxTokens": self._max_tokens},
        }
        if self._tool_config:
            kwargs["toolConfig"] = self._tool_config
        response = self._client.converse(**kwargs)
        msg = response["output"]["message"]
        self._messages.append(msg)
        usage = response.get("usage", {})
        _track_usage(usage.get("inputTokens", 0), usage.get("outputTokens", 0))
        self._maybe_window()
        texts, tool_calls = [], []
        for item in msg.get("content", []):
            if "text" in item:
                texts.append(item["text"])
            elif "toolUse" in item:
                tu = item["toolUse"]
                tool_calls.append(ToolCall(id=tu["toolUseId"], name=tu["name"], args=tu["input"]))
        return ChatResponse(
            text="".join(texts) or None,
            tool_calls=tool_calls,
            stop_reason=_norm_stop_reason(response.get("stopReason")),
        )


# ── factory + one-shot streaming ──────────────────────────────────────────────

_OPENAI_COMPAT = {"openrouter", "groq", "github"}


def _is_bedrock_claude_model() -> bool:
    """True if AI_MODEL is a Claude model on Bedrock (Messages API / AnthropicBedrock).

    Matches both the bare foundation ID (``anthropic.claude-…``) and cross-region
    inference profiles (``us.anthropic.claude-…``, ``eu.``, ``apac.``,
    ``global.anthropic.claude-…``). Non-Claude models (``meta.``, ``mistral.``,
    ``amazon.``) go through the boto3 Converse path instead.
    """
    return "anthropic.claude" in AI_MODEL


def _ensure_known_provider() -> None:
    """Fail loudly on an unknown AI_PROVIDER instead of silently using Gemini."""
    if AI_PROVIDER not in KNOWN_PROVIDERS:
        valid = ", ".join(sorted(KNOWN_PROVIDERS))
        raise RuntimeError(f"Unknown AI_PROVIDER '{AI_PROVIDER}'. Set AI_PROVIDER in your .env to one of: {valid}.")


def create_chat_session(system: str, tools: list[dict] | None = None, max_tokens: int = _DEFAULT_MAX_TOKENS):
    _ensure_known_provider()
    if AI_PROVIDER == "claude":
        return ClaudeChatSession(system=system, tools=tools, max_tokens=max_tokens)
    if AI_PROVIDER in _OPENAI_COMPAT:
        return OpenAICompatibleChatSession(system=system, tools=tools, max_tokens=max_tokens)
    if AI_PROVIDER == "bedrock":
        if _is_bedrock_claude_model():
            return BedrockChatSession(system=system, tools=tools, max_tokens=max_tokens)
        return BedrockConverseChatSession(system=system, tools=tools, max_tokens=max_tokens)
    return GeminiChatSession(system=system, tools=tools, max_tokens=max_tokens)


def stream_complete(prompt: str, system: str, max_tokens: int = _DEFAULT_MAX_TOKENS) -> Iterator[str]:
    """One-shot streaming completion with no session state or tool calls."""
    _ensure_known_provider()
    if AI_PROVIDER == "claude":
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        cached_system: Any = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        with client.messages.stream(
            model=AI_MODEL,
            system=cached_system,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
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

        oa_client = OpenAI(base_url=PROVIDER_BASE_URLS[AI_PROVIDER], api_key=get_provider_api_key())
        oa_stream = oa_client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            stream=True,
        )
        for oa_chunk in oa_stream:
            if oa_chunk.choices and oa_chunk.choices[0].delta.content:
                yield oa_chunk.choices[0].delta.content

    elif AI_PROVIDER == "bedrock":
        if _is_bedrock_claude_model():
            client = _anthropic_bedrock_client()
            cached_system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            with client.messages.stream(
                model=AI_MODEL,
                system=cached_system,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
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
                inferenceConfig={"maxTokens": max_tokens},
            )
            for event in response.get("stream", []):
                if "contentBlockDelta" in event:
                    delta = event["contentBlockDelta"].get("delta", {})
                    if "text" in delta:
                        yield delta["text"]

    else:
        from google import genai
        from google.genai import types

        genai_client = genai.Client(api_key=GEMINI_API_KEY)
        for g_chunk in genai_client.models.generate_content_stream(
            model=AI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.4),
        ):
            if g_chunk.text:
                yield g_chunk.text


def _loads_lenient(raw: str) -> dict:
    """Parse a JSON object, tolerating stray markdown fences around it."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


def complete_json(prompt: str, system: str, max_tokens: int = _DEFAULT_MAX_TOKENS) -> dict:
    """One-shot completion that returns a parsed JSON object.

    Uses each provider's native JSON/structured mode (or an assistant ``{`` prefill
    for Anthropic models, which lack a JSON mode) so the result is reliably parseable.
    A malformed free-text JSON would otherwise waste the entire output.
    """
    _ensure_known_provider()
    if AI_PROVIDER == "claude":
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        cached_system: Any = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        response = client.messages.create(
            model=AI_MODEL,
            system=cached_system,
            messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "{"},
            ],
            max_tokens=max_tokens,
        )
        _track_usage(
            response.usage.input_tokens,
            response.usage.output_tokens,
            getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return _loads_lenient("{" + text)

    if AI_PROVIDER in _OPENAI_COMPAT:
        from openai import OpenAI

        oa_client = OpenAI(base_url=PROVIDER_BASE_URLS[AI_PROVIDER], api_key=get_provider_api_key())
        oa_response = oa_client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        if oa_response.usage:
            _track_usage(oa_response.usage.prompt_tokens, oa_response.usage.completion_tokens)
        return _loads_lenient(oa_response.choices[0].message.content or "")

    if AI_PROVIDER == "bedrock":
        if _is_bedrock_claude_model():
            client = _anthropic_bedrock_client()
            cached_system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            response = client.messages.create(
                model=AI_MODEL,
                system=cached_system,
                messages=[
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "{"},
                ],
                max_tokens=max_tokens,
            )
            _track_usage(
                response.usage.input_tokens,
                response.usage.output_tokens,
                getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            )
            text = "".join(b.text for b in response.content if b.type == "text")
            return _loads_lenient("{" + text)

        client = _boto3_bedrock_client()
        response = client.converse(
            modelId=AI_MODEL,
            system=[{"text": system}],
            messages=[
                {"role": "user", "content": [{"text": prompt}]},
                {"role": "assistant", "content": [{"text": "{"}]},
            ],
            inferenceConfig={"maxTokens": max_tokens},
        )
        usage = response.get("usage", {})
        _track_usage(usage.get("inputTokens", 0), usage.get("outputTokens", 0))
        msg = response["output"]["message"]
        text = "".join(item.get("text", "") for item in msg.get("content", []))
        return _loads_lenient("{" + text)

    from google import genai
    from google.genai import types

    genai_client = genai.Client(api_key=GEMINI_API_KEY)
    g_response = genai_client.models.generate_content(
        model=AI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.4,
            response_mime_type="application/json",
        ),
    )
    um = getattr(g_response, "usage_metadata", None)
    if um:
        _track_usage(
            getattr(um, "prompt_token_count", 0) or 0,
            getattr(um, "candidates_token_count", 0) or 0,
        )
    return _loads_lenient(g_response.text or "")


def provider_label() -> str:
    labels = {
        "openrouter": "OpenRouter",
        "groq": "Groq",
        "github": "GitHub Models",
        "bedrock": "Amazon Bedrock",
        "claude": "Claude",
        "gemini": "Gemini",
    }
    name = labels.get(AI_PROVIDER, AI_PROVIDER.capitalize())
    return f"{name} ({AI_MODEL})"
