"""Tests for debug log rotation (count-based: keep the newest N daily files)."""

from datetime import datetime, timedelta

import debug_log


def _make_log(logs_dir, days_ago: int):
    date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    path = logs_dir / f"debug-{date}.log"
    path.write_text("x\n")
    return path


def test_prune_keeps_newest_max_files(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_log, "LOGS_DIR", tmp_path)
    # 20 files with gaps between dates — only the 14 newest survive
    files = [
        _make_log(tmp_path, d) for d in (0, 1, 2, 5, 8, 9, 12, 20, 21, 30, 40, 45, 50, 60, 70, 80, 90, 120, 150, 200)
    ]

    removed = debug_log.prune_old_logs()

    assert removed == 6
    assert all(p.exists() for p in files[:14])
    assert not any(p.exists() for p in files[14:])


def test_prune_below_limit_removes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_log, "LOGS_DIR", tmp_path)
    # sparse usage: old dates, but few files — all kept
    files = [_make_log(tmp_path, d) for d in (0, 30, 90, 365)]

    assert debug_log.prune_old_logs() == 0
    assert all(p.exists() for p in files)


def test_prune_ignores_other_files(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_log, "LOGS_DIR", tmp_path)
    other = tmp_path / "notes.log"
    other.write_text("keep me\n")
    [_make_log(tmp_path, d) for d in range(20)]

    debug_log.prune_old_logs()

    assert other.exists()


def test_prune_missing_dir_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_log, "LOGS_DIR", tmp_path / "does-not-exist")
    assert debug_log.prune_old_logs() == 0


def test_custom_max_files(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_log, "LOGS_DIR", tmp_path)
    files = [_make_log(tmp_path, d) for d in range(5)]

    assert debug_log.prune_old_logs(max_files=2) == 3
    assert files[0].exists() and files[1].exists()
    assert not any(p.exists() for p in files[2:])


# ── error() — always-on error logging with tracebacks ─────────────────────────


def _today_log(logs_dir):
    date = datetime.now().strftime("%Y-%m-%d")
    return logs_dir / f"debug-{date}.log"


def test_error_writes_even_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_log, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(debug_log, "_enabled", False)

    debug_log.error("TEST", "something broke", code=42)

    content = _today_log(tmp_path).read_text()
    assert "something broke" in content
    assert "code=42" in content


def test_error_includes_traceback(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_log, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(debug_log, "_enabled", False)

    try:
        raise ValueError("boom")
    except ValueError as e:
        debug_log.error("TEST", "caught", exc=e)

    content = _today_log(tmp_path).read_text()
    assert "Traceback" in content
    assert "ValueError: boom" in content


def test_log_still_gated_by_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_log, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(debug_log, "_enabled", False)

    debug_log.log("TEST", "chatty line")

    assert not _today_log(tmp_path).exists()


def test_error_never_raises_on_unwritable_dir(tmp_path, monkeypatch):
    target = tmp_path / "ro"
    target.mkdir()
    target.chmod(0o444)
    monkeypatch.setattr(debug_log, "LOGS_DIR", target / "sub")
    try:
        debug_log.error("TEST", "should not raise", exc=ValueError("x"))
    finally:
        target.chmod(0o755)


def test_logs_dir_env_override(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "LOGS_DIR", str(tmp_path / "custom"))
    assert debug_log.logs_dir() == tmp_path / "custom"


def test_logs_dir_relative_override_falls_back(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "LOGS_DIR", "relative/path")
    monkeypatch.setattr(debug_log, "LOGS_DIR", tmp_path)
    assert debug_log.logs_dir() == tmp_path
