"""Tests for _stamp_routine — watermark appended to every pushed routine."""


def test_stamp_no_notes():
    from ai.tools import _stamp_routine

    result = _stamp_routine({"title": "Push Day", "exercises": []})
    assert result["notes"] == "✦ Powered by Lifter"


def test_stamp_with_existing_notes():
    from ai.tools import _stamp_routine

    result = _stamp_routine({"title": "Pull Day", "notes": "Focus on back width.", "exercises": []})
    assert result["notes"] == "Focus on back width.\n\n✦ Powered by Lifter"


def test_stamp_empty_string_notes():
    from ai.tools import _stamp_routine

    result = _stamp_routine({"title": "Legs", "notes": "", "exercises": []})
    assert result["notes"] == "✦ Powered by Lifter"


def test_stamp_whitespace_only_notes():
    from ai.tools import _stamp_routine

    result = _stamp_routine({"title": "Legs", "notes": "   ", "exercises": []})
    assert result["notes"] == "✦ Powered by Lifter"


def test_stamp_returns_copy_not_mutation():
    from ai.tools import _stamp_routine

    original = {"title": "Test", "exercises": []}
    result = _stamp_routine(original)
    assert "notes" not in original
    assert result is not original


def test_stamp_preserves_all_other_fields():
    from ai.tools import _stamp_routine

    original = {"title": "Squat Day", "exercises": [{"id": "abc"}]}
    result = _stamp_routine(original)
    assert result["title"] == "Squat Day"
    assert result["exercises"] == [{"id": "abc"}]


def test_stamp_watermark_text():
    from ai.tools import _stamp_routine

    result = _stamp_routine({"title": "Any", "exercises": []})
    assert "Lifter" in result["notes"]
    assert "✦" in result["notes"]
