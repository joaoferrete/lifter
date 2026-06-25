import os
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

HEVY_API_KEY: str = os.environ.get("HEVY_API_KEY", "")
DEFAULT_LANGUAGE: str = os.environ.get("DEFAULT_LANGUAGE", "en")
BASE_URL: str = "https://api.hevyapp.com"
_raw_db = os.environ.get("DB_PATH")
DB_PATH: Path = (
    Path(_raw_db) if (_raw_db and Path(_raw_db).is_absolute())
    else _PROJECT_DIR / (_raw_db or "hevy.db")
)

# ── AI provider ───────────────────────────────────────────────────────────────

AI_PROVIDER: str = os.environ.get("AI_PROVIDER", "gemini").lower()
# gemini | claude | openrouter | groq | github | bedrock

# Recognized providers — used to fail loudly on a typo instead of silently
# falling back to Gemini (see ai/provider.py and cli._require_ai).
KNOWN_PROVIDERS: frozenset[str] = frozenset(
    {"gemini", "claude", "openrouter", "groq", "github", "bedrock"}
)

# API keys — only the one matching AI_PROVIDER needs to be set
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "")

# Amazon Bedrock — two auth paths:
#   1. Bearer token (AWS_BEARER_TOKEN_BEDROCK) — works for Claude models with no
#      boto3 install required.
#   2. AWS credentials via boto3 (pip install anthropic[bedrock]).
AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID: str = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY: str = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_SESSION_TOKEN: str = os.environ.get("AWS_SESSION_TOKEN", "")
# Amazon Bedrock API key (bearer token). Canonical AWS env var name, so botocore
# also picks it up automatically on the non-Claude Converse path.
AWS_BEARER_TOKEN_BEDROCK: str = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")

# OpenAI-compatible provider base URLs
PROVIDER_BASE_URLS: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "groq":       "https://api.groq.com/openai/v1",
    "github":     "https://models.github.ai/inference",
}

# Default model per provider (overridden by AI_MODEL env var)
_DEFAULT_MODELS: dict[str, str] = {
    "gemini":      "gemini-flash-latest",
    "claude":      "claude-opus-4-8",
    "openrouter":  "openrouter/owl-alpha",
    "groq":        "openai/gpt-oss-120b",
    "github":      "openai/gpt-4o",  # GitHub Models requires the publisher/ prefix
    "bedrock":     "us.anthropic.claude-haiku-4-5-20251001-v1:0",
}

AI_MODEL: str = (
    os.environ.get("AI_MODEL")
    or os.environ.get("GEMINI_MODEL")           # backward compat
    or _DEFAULT_MODELS.get(AI_PROVIDER, "gemini-flash-latest")
)


def get_provider_api_key() -> str:
    """Return the API key for the currently configured provider."""
    return {
        "gemini":     GEMINI_API_KEY,
        "claude":     ANTHROPIC_API_KEY,
        "openrouter": OPENROUTER_API_KEY,
        "groq":       GROQ_API_KEY,
        "github":     GITHUB_TOKEN,
        "bedrock":    "",   # uses boto3 / env-based AWS credentials
    }.get(AI_PROVIDER, "")
