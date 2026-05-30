import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

HEVY_API_KEY: str = os.environ.get("HEVY_API_KEY", "")
BASE_URL: str = "https://api.hevyapp.com"
DB_PATH: Path = Path(os.environ.get("DB_PATH", "hevy.db"))

# ── AI provider ───────────────────────────────────────────────────────────────

AI_PROVIDER: str = os.environ.get("AI_PROVIDER", "gemini").lower()
# gemini | claude | openrouter | groq | github | bedrock

# API keys — only the one matching AI_PROVIDER needs to be set
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "")

# Amazon Bedrock — uses boto3 (pip install anthropic[bedrock])
AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID: str = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY: str = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_SESSION_TOKEN: str = os.environ.get("AWS_SESSION_TOKEN", "")

# OpenAI-compatible provider base URLs
PROVIDER_BASE_URLS: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "groq":       "https://api.groq.com/openai/v1",
    "github":     "https://models.inference.ai.azure.com",
}

# Default model per provider (overridden by AI_MODEL env var)
_DEFAULT_MODELS: dict[str, str] = {
    "gemini":      "gemini-2.5-pro",
    "claude":      "claude-opus-4-8",
    "openrouter":  "anthropic/claude-3-5-sonnet",
    "groq":        "llama-3.3-70b-versatile",
    "github":      "gpt-4o",
    "bedrock":     "anthropic.claude-3-5-sonnet-20241022-v2:0",
}

AI_MODEL: str = (
    os.environ.get("AI_MODEL")
    or os.environ.get("GEMINI_MODEL")           # backward compat
    or _DEFAULT_MODELS.get(AI_PROVIDER, "gemini-2.5-pro")
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
