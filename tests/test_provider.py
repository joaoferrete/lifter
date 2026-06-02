"""Tests for AI provider dispatch, labelling, and the ToolCall dataclass."""
from unittest.mock import patch, MagicMock

import ai.provider as provider_mod


# ── provider_label ────────────────────────────────────────────────────────────

def test_label_gemini(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(provider_mod, "AI_MODEL", "gemini-2.5-pro")
    assert provider_mod.provider_label() == "Gemini (gemini-2.5-pro)"


def test_label_claude(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "claude")
    monkeypatch.setattr(provider_mod, "AI_MODEL", "claude-opus-4-8")
    assert provider_mod.provider_label() == "Claude (claude-opus-4-8)"


def test_label_openrouter(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "openrouter")
    monkeypatch.setattr(provider_mod, "AI_MODEL", "anthropic/claude-3-5-sonnet")
    assert provider_mod.provider_label() == "OpenRouter (anthropic/claude-3-5-sonnet)"


def test_label_groq(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "groq")
    monkeypatch.setattr(provider_mod, "AI_MODEL", "llama-3.3-70b-versatile")
    assert provider_mod.provider_label() == "Groq (llama-3.3-70b-versatile)"


def test_label_github(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "github")
    monkeypatch.setattr(provider_mod, "AI_MODEL", "gpt-4o")
    assert provider_mod.provider_label() == "GitHub Models (gpt-4o)"


def test_label_bedrock(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "bedrock")
    monkeypatch.setattr(provider_mod, "AI_MODEL", "anthropic.claude-3-5-sonnet-20241022-v2:0")
    assert provider_mod.provider_label() == "Amazon Bedrock (anthropic.claude-3-5-sonnet-20241022-v2:0)"


def test_label_unknown_provider_capitalised(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "foobar")
    monkeypatch.setattr(provider_mod, "AI_MODEL", "model-x")
    assert provider_mod.provider_label() == "Foobar (model-x)"


# ── ToolCall / ChatResponse dataclasses ───────────────────────────────────────

def test_tool_call_fields():
    from ai.provider import ToolCall
    tc = ToolCall(id="tc1", name="push_routine", args={"title": "Push"})
    assert tc.id == "tc1"
    assert tc.name == "push_routine"
    assert tc.args == {"title": "Push"}


def test_chat_response_defaults():
    from ai.provider import ChatResponse
    resp = ChatResponse(text="hello")
    assert resp.text == "hello"
    assert resp.tool_calls == []


def test_chat_response_with_tool_calls():
    from ai.provider import ChatResponse, ToolCall
    tc = ToolCall(id="x", name="fn", args={})
    resp = ChatResponse(text=None, tool_calls=[tc])
    assert resp.text is None
    assert len(resp.tool_calls) == 1


# ── create_chat_session dispatch ──────────────────────────────────────────────

def test_dispatch_claude(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "claude")
    with patch.object(provider_mod, "ClaudeChatSession") as MockClass:
        MockClass.return_value = MagicMock()
        provider_mod.create_chat_session("sys")
    MockClass.assert_called_once_with(system="sys", tools=None)


def test_dispatch_claude_passes_tools(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "claude")
    tools = [{"name": "fn"}]
    with patch.object(provider_mod, "ClaudeChatSession") as MockClass:
        MockClass.return_value = MagicMock()
        provider_mod.create_chat_session("sys", tools=tools)
    MockClass.assert_called_once_with(system="sys", tools=tools)


def test_dispatch_openrouter(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "openrouter")
    with patch.object(provider_mod, "OpenAICompatibleChatSession") as MockClass:
        MockClass.return_value = MagicMock()
        provider_mod.create_chat_session("sys")
    MockClass.assert_called_once_with(system="sys", tools=None)


def test_dispatch_groq(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "groq")
    with patch.object(provider_mod, "OpenAICompatibleChatSession") as MockClass:
        MockClass.return_value = MagicMock()
        provider_mod.create_chat_session("sys")
    MockClass.assert_called_once()


def test_dispatch_github(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "github")
    with patch.object(provider_mod, "OpenAICompatibleChatSession") as MockClass:
        MockClass.return_value = MagicMock()
        provider_mod.create_chat_session("sys")
    MockClass.assert_called_once()


def test_dispatch_bedrock_claude_model(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "bedrock")
    monkeypatch.setattr(provider_mod, "AI_MODEL", "anthropic.claude-3-5-sonnet-20241022-v2:0")
    with patch.object(provider_mod, "BedrockChatSession") as MockClass:
        MockClass.return_value = MagicMock()
        provider_mod.create_chat_session("sys")
    MockClass.assert_called_once()


def test_dispatch_bedrock_non_claude_model(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "bedrock")
    monkeypatch.setattr(provider_mod, "AI_MODEL", "meta.llama3-70b-instruct-v1:0")
    with patch.object(provider_mod, "BedrockConverseChatSession") as MockClass:
        MockClass.return_value = MagicMock()
        provider_mod.create_chat_session("sys")
    MockClass.assert_called_once()


def test_dispatch_gemini_is_default(monkeypatch):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "gemini")
    with patch.object(provider_mod, "GeminiChatSession") as MockClass:
        MockClass.return_value = MagicMock()
        provider_mod.create_chat_session("sys")
    MockClass.assert_called_once()
