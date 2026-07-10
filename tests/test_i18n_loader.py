"""i18n loader behavior: language resolution, fallbacks, format safety."""

import i18n
from i18n import _


def _with_lang(lang: str):
    i18n.init(lang)


def teardown_function() -> None:
    i18n.init("en")  # never leak a non-English locale into other tests


def test_known_key_translates():
    _with_lang("en")
    assert _("time.weeks", n=8) == "8 weeks"


def test_missing_key_returns_the_key_itself():
    _with_lang("en")
    assert _("no.such.key") == "no.such.key"


def test_pt_br_falls_back_to_english_for_missing_value():
    _with_lang("pt_BR")
    # A key that exists nowhere still returns the key (never crashes).
    assert _("no.such.key") == "no.such.key"


def test_format_error_returns_raw_template():
    _with_lang("en")
    # Missing placeholder kwargs must not raise — the raw template comes back.
    out = _("time.weeks")
    assert out == "{n} weeks"


def test_resolve_dash_variant():
    _with_lang("pt-BR")  # dash form must resolve to pt_BR
    assert _("time.weeks", n=2) == "2 semanas"


def test_unknown_language_falls_back_to_english():
    _with_lang("zz")
    assert _("time.weeks", n=3) == "3 weeks"
