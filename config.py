import os
from pathlib import Path

from dotenv import load_dotenv

import paths

# ── AI provider constants ─────────────────────────────────────────────────────

BASE_URL: str = "https://api.hevyapp.com"

# Recognized providers — used to fail loudly on a typo instead of silently
# falling back to Gemini (see ai/provider.py and cli._require_ai).
KNOWN_PROVIDERS: frozenset[str] = frozenset({"gemini", "claude", "openrouter", "groq", "github", "bedrock"})

# OpenAI-compatible provider base URLs
PROVIDER_BASE_URLS: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "github": "https://models.github.ai/inference",
}

# Default model per provider (overridden by AI_MODEL env var)
_DEFAULT_MODELS: dict[str, str] = {
    "gemini": "gemini-flash-latest",
    "claude": "claude-opus-4-8",
    "openrouter": "openrouter/owl-alpha",
    "groq": "openai/gpt-oss-120b",
    "github": "openai/gpt-4o",  # GitHub Models requires the publisher/ prefix
    "bedrock": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
}

# Names pushed into ai.provider (which copies them at import time) whenever
# the environment is reloaded — see _sync_provider_module().
_PROVIDER_SYNC_NAMES: tuple[str, ...] = (
    "AI_PROVIDER",
    "AI_MODEL",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "GITHUB_TOKEN",
    "AWS_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
)


# Set by profile_mgr.activate_profile(). Wins over the env-derived DB_PATH so
# reload_env() can't repoint the app at the default database mid-session.
PROFILE_DB_PATH: Path | None = None


def _resolve_db_path(raw: str | None) -> Path:
    if raw and Path(raw).is_absolute():
        return Path(raw)
    return paths.DATA_DIR / (raw or "hevy.db")


def _load_globals() -> None:
    """(Re)populate every env-derived module global from os.environ."""
    global \
        HEVY_API_KEY, \
        DEFAULT_LANGUAGE, \
        DB_PATH, \
        AI_PROVIDER, \
        AI_MODEL, \
        GEMINI_API_KEY, \
        ANTHROPIC_API_KEY, \
        OPENROUTER_API_KEY, \
        GROQ_API_KEY, \
        GITHUB_TOKEN, \
        AWS_REGION, \
        AWS_ACCESS_KEY_ID, \
        AWS_SECRET_ACCESS_KEY, \
        AWS_SESSION_TOKEN, \
        AWS_BEARER_TOKEN_BEDROCK, \
        _ENV_AI_PROVIDER, \
        _ENV_AI_MODEL, \
        EXPORT_DIR, \
        LOGS_DIR

    HEVY_API_KEY = os.environ.get("HEVY_API_KEY", "")
    DEFAULT_LANGUAGE = os.environ.get("DEFAULT_LANGUAGE", "en")
    DB_PATH = PROFILE_DB_PATH or _resolve_db_path(os.environ.get("DB_PATH"))

    # Optional overrides for where exports and logs are written (global,
    # shared across profiles). Empty = built-in defaults: exports next to the
    # active profile's DB, logs under paths.LOGS_DIR.
    EXPORT_DIR = os.environ.get("EXPORT_DIR", "").strip()
    LOGS_DIR = os.environ.get("LOGS_DIR", "").strip()

    AI_PROVIDER = os.environ.get("AI_PROVIDER", "gemini").lower()
    # gemini | claude | openrouter | groq | github | bedrock

    # API keys — only the one matching AI_PROVIDER needs to be set
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

    # Amazon Bedrock — two auth paths:
    #   1. Bearer token (AWS_BEARER_TOKEN_BEDROCK) — works for Claude models with no
    #      boto3 install required.
    #   2. AWS credentials via boto3 (pip install anthropic[bedrock]).
    AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
    AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    AWS_SESSION_TOKEN = os.environ.get("AWS_SESSION_TOKEN", "")
    # Amazon Bedrock API key (bearer token). Canonical AWS env var name, so botocore
    # also picks it up automatically on the non-Claude Converse path.
    AWS_BEARER_TOKEN_BEDROCK = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")

    AI_MODEL = (
        os.environ.get("AI_MODEL")
        or os.environ.get("GEMINI_MODEL")  # backward compat
        or _DEFAULT_MODELS.get(AI_PROVIDER, "gemini-flash-latest")
    )

    # Snapshots of the env-resolved values — apply_ai_overrides() falls back to
    # these when the per-profile prefs are unset or invalid.
    _ENV_AI_PROVIDER = AI_PROVIDER
    _ENV_AI_MODEL = AI_MODEL


load_dotenv(paths.ENV_FILE)
_load_globals()


def _sync_provider_module() -> None:
    """Push current config values into ai.provider's import-time copies."""
    import sys

    provider_mod = sys.modules.get("ai.provider")
    if provider_mod is None:
        return
    for name in _PROVIDER_SYNC_NAMES:
        if hasattr(provider_mod, name):
            setattr(provider_mod, name, globals()[name])


def reload_env() -> None:
    """Re-read the .env file and refresh every derived global.

    Uses override=True because stale values are already in os.environ — the
    in-app key editor writes empty values (KEY=) rather than deleting lines,
    so clearing a key works too.
    """
    load_dotenv(paths.ENV_FILE, override=True)
    _load_globals()
    _sync_provider_module()


def set_env_values(updates: dict[str, str], env_file: Path | None = None) -> None:
    """Line-based upsert of KEY=value pairs into the .env file.

    Preserves comments, unknown lines and ordering; missing keys are appended.
    Values are written verbatim (keys/tokens contain no spaces). The write is
    atomic (tmp file + os.replace) and the file ends up chmod 0600.
    """
    import re

    target = env_file or paths.ENV_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = target.read_text(encoding="utf-8").splitlines(keepends=True) if target.exists() else []

    pending = dict(updates)
    for i, line in enumerate(lines):
        for key in list(pending):
            if re.match(rf"\s*(?:export\s+)?{re.escape(key)}\s*=", line):
                lines[i] = f"{key}={pending.pop(key)}\n"
                break
    for key, value in pending.items():
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, target)


def export_dir() -> Path:
    """Directory exports are written to (and imports discovered in): the
    EXPORT_DIR .env override when set, else exports/ next to the active
    profile's database."""
    if EXPORT_DIR:
        return Path(EXPORT_DIR).expanduser()
    return DB_PATH.parent / "exports"


def default_model_for(provider: str) -> str:
    return _DEFAULT_MODELS.get(provider, "gemini-flash-latest")


def get_provider_api_key(provider: str | None = None) -> str:
    """Return the API key for the given provider (default: the configured one)."""
    return {
        "gemini": GEMINI_API_KEY,
        "claude": ANTHROPIC_API_KEY,
        "openrouter": OPENROUTER_API_KEY,
        "groq": GROQ_API_KEY,
        "github": GITHUB_TOKEN,
        "bedrock": "",  # uses boto3 / env-based AWS credentials
    }.get(provider or AI_PROVIDER, "")


def apply_ai_overrides() -> None:
    """Apply the per-profile ai_provider/ai_model prefs over the .env config.

    Call after the profile DB is open, and again after changing the prefs.
    ai/provider.py takes import-time copies of these globals (and its tests
    monkeypatch them), so we sync it here instead of refactoring it.
    """
    global AI_PROVIDER, AI_MODEL
    try:
        from db.goals import get_pref  # lazy — db.store imports config

        pref_provider = (get_pref("ai_provider") or "").strip().lower()
        pref_model = (get_pref("ai_model") or "").strip()
    except Exception:
        pref_provider = pref_model = ""

    AI_PROVIDER = pref_provider if pref_provider in KNOWN_PROVIDERS else _ENV_AI_PROVIDER
    if pref_model:
        AI_MODEL = pref_model
    elif AI_PROVIDER == _ENV_AI_PROVIDER:
        AI_MODEL = _ENV_AI_MODEL
    else:
        # the env AI_MODEL was chosen for the env provider — a different
        # provider needs its own default, model names are not portable
        AI_MODEL = default_model_for(AI_PROVIDER)

    _sync_provider_module()
