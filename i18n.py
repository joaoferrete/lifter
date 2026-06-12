"""Lightweight i18n for Lifter CLI.

Usage:
    from i18n import _
    print(_("menu.sync"))
    print(_("error.rate_limit_429", retry_after=30))

Call i18n.init(lang) to change the active language at any point.
"""
import json
from pathlib import Path

_LOCALES_DIR = Path(__file__).resolve().parent / "locales"
_SUPPORTED: set = {"en", "pt_BR"}
_DEFAULT_LANG = "en"


def _resolve_lang(lang: str) -> str:
    if lang in _SUPPORTED:
        return lang
    normalized = lang.replace("-", "_")
    if normalized in _SUPPORTED:
        return normalized
    prefix = lang.split("_")[0].split("-")[0]
    for supported in sorted(_SUPPORTED):
        if supported.startswith(prefix):
            return supported
    return _DEFAULT_LANG


def _load(lang: str) -> dict:
    path = _LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


class _Translator:
    def __init__(self) -> None:
        self._lang: str = _DEFAULT_LANG
        self._strings: dict = _load(_DEFAULT_LANG)
        self._fallback: dict = {}

    def init(self, lang: str) -> None:
        resolved = _resolve_lang(lang)
        self._lang = resolved
        self._strings = _load(resolved)
        self._fallback = _load(_DEFAULT_LANG) if resolved != _DEFAULT_LANG else {}

    def translate(self, _key: str, **kwargs) -> str:
        text = self._strings.get(_key) or self._fallback.get(_key)
        if text is None:
            return _key
        if kwargs:
            try:
                return text.format_map(kwargs)
            except (KeyError, ValueError):
                return text
        return text


_translator = _Translator()
_ = _translator.translate


def init(lang: str) -> None:
    """Initialize or re-initialize translation. Safe to call multiple times."""
    _translator.init(lang)
