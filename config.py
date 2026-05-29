import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

HEVY_API_KEY: str = os.environ.get("HEVY_API_KEY", "")
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
BASE_URL: str = "https://api.hevyapp.com"
DB_PATH: Path = Path(os.environ.get("DB_PATH", "hevy.db"))

AI_PROVIDER: str = os.environ.get("AI_PROVIDER", "gemini").lower()  # "gemini" | "claude"

_DEFAULT_MODELS = {"gemini": "gemini-2.5-pro", "claude": "claude-opus-4-8"}

# AI_MODEL overrides everything; falls back to GEMINI_MODEL for backward compat
AI_MODEL: str = (
    os.environ.get("AI_MODEL")
    or os.environ.get("GEMINI_MODEL")
    or _DEFAULT_MODELS.get(AI_PROVIDER, "gemini-2.5-pro")
)
