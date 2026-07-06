"""Locale files must stay key-synced — the translator falls back to English
silently, so drift between en.json and pt_BR.json is invisible at runtime."""
import json
import string
from pathlib import Path

import pytest

_LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"


def _load(name: str) -> dict:
    return json.loads((_LOCALES_DIR / name).read_text(encoding="utf-8"))


def test_locale_keys_match():
    en = _load("en.json")
    pt = _load("pt_BR.json")

    missing_in_pt = sorted(set(en) - set(pt))
    missing_in_en = sorted(set(pt) - set(en))
    assert not missing_in_pt, f"keys missing in pt_BR.json: {missing_in_pt}"
    assert not missing_in_en, f"keys missing in en.json: {missing_in_en}"


def _placeholders(text: str) -> set:
    return {field for _, field, _, _ in string.Formatter().parse(text) if field}


def test_locale_placeholders_match():
    en = _load("en.json")
    pt = _load("pt_BR.json")

    mismatched = {
        key: (sorted(_placeholders(en[key])), sorted(_placeholders(pt[key])))
        for key in set(en) & set(pt)
        if _placeholders(en[key]) != _placeholders(pt[key])
    }
    assert not mismatched, f"placeholder mismatch (en vs pt_BR): {mismatched}"
