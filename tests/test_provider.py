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


# ── complete_json ─────────────────────────────────────────────────────────────

def test_loads_lenient_plain_json():
    assert provider_mod._loads_lenient('{"a": 1}') == {"a": 1}


def test_loads_lenient_strips_markdown_fences():
    raw = '```json\n{"a": 1, "b": "x"}\n```'
    assert provider_mod._loads_lenient(raw) == {"a": 1, "b": "x"}


def test_complete_json_openai_compat_uses_json_mode(monkeypatch, tmp_db):
    from db.goals import get_token_usage
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "openrouter")
    monkeypatch.setattr(provider_mod, "AI_MODEL", "anthropic/claude-3-5-sonnet")

    captured = {}
    mock_client = MagicMock()

    def fake_create(**kwargs):
        captured.update(kwargs)
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content='{"score": 90}'))]
        resp.usage = MagicMock(prompt_tokens=120, completion_tokens=30)
        return resp

    mock_client.chat.completions.create.side_effect = fake_create

    with patch("openai.OpenAI", return_value=mock_client):
        result = provider_mod.complete_json("analyse", system="you are a coach")

    assert result == {"score": 90}
    assert captured["response_format"] == {"type": "json_object"}
    usage = get_token_usage()
    assert usage["input"] == 120 and usage["output"] == 30


def test_complete_json_claude_prefills_brace(monkeypatch, tmp_db):
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "claude")
    monkeypatch.setattr(provider_mod, "AI_MODEL", "claude-opus-4-8")

    captured = {}
    mock_client = MagicMock()

    def fake_create(**kwargs):
        captured.update(kwargs)
        block = MagicMock()
        block.type = "text"
        block.text = '"score": 88}'  # model continues from the prefilled "{"
        resp = MagicMock()
        resp.content = [block]
        resp.usage = MagicMock(input_tokens=200, output_tokens=20, cache_read_input_tokens=0)
        return resp

    mock_client.messages.create.side_effect = fake_create

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = provider_mod.complete_json("analyse", system="you are a coach")

    assert result == {"score": 88}
    # Assistant turn must be prefilled with "{" to force JSON output.
    assert captured["messages"][-1] == {"role": "assistant", "content": "{"}


# ── _track_usage ──────────────────────────────────────────────────────────────

def test_track_usage_calls_add_token_usage(tmp_db):
    from db.goals import get_token_usage
    provider_mod._track_usage(input_tokens=100, output_tokens=50)
    usage = get_token_usage()
    assert usage["input"] == 100
    assert usage["output"] == 50


def test_track_usage_records_cache_read(tmp_db):
    from db.goals import get_token_usage
    provider_mod._track_usage(input_tokens=200, output_tokens=40, cache_read=150)
    usage = get_token_usage()
    assert usage["cache_read"] == 150


def test_track_usage_is_cumulative_across_calls(tmp_db):
    from db.goals import get_token_usage
    provider_mod._track_usage(input_tokens=300, output_tokens=60)
    provider_mod._track_usage(input_tokens=200, output_tokens=40)
    usage = get_token_usage()
    assert usage["input"] == 500
    assert usage["output"] == 100


def test_track_usage_skips_all_zeros(tmp_db):
    from db.goals import get_token_usage
    provider_mod._track_usage()  # nothing — should not write any row
    usage = get_token_usage()
    assert usage == {"input": 0, "output": 0, "cache_read": 0}


def test_track_usage_never_raises_on_db_error(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("db exploded")
    monkeypatch.setattr("db.goals.add_token_usage", boom)
    provider_mod._track_usage(input_tokens=999)  # must not raise
