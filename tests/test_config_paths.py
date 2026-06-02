"""Tests for path resolution in config.py and fit/auth.py."""
from pathlib import Path


# ── live module values ────────────────────────────────────────────────────────

def test_project_dir_is_config_file_parent():
    import config
    assert config._PROJECT_DIR == Path(config.__file__).resolve().parent


def test_db_path_is_under_project_dir():
    import config
    assert config.DB_PATH.parent == config._PROJECT_DIR


def test_credentials_file_is_under_project_dir():
    from fit.auth import CREDENTIALS_FILE
    import config
    assert CREDENTIALS_FILE.parent == config._PROJECT_DIR


def test_token_file_is_sibling_of_db():
    from fit.auth import TOKEN_FILE
    from config import DB_PATH
    assert TOKEN_FILE.parent == DB_PATH.parent


# ── resolution logic (mirrors config.py, tested in isolation) ─────────────────

def _resolve_db_path(raw, project_dir):
    if raw and Path(raw).is_absolute():
        return Path(raw)
    return project_dir / (raw or "hevy.db")


def test_resolution_absolute_env_var(tmp_path):
    abs_path = tmp_path / "custom.db"
    assert _resolve_db_path(str(abs_path), Path("/project")) == abs_path


def test_resolution_relative_env_var_uses_project_dir(tmp_path):
    assert _resolve_db_path("mydb.db", tmp_path) == tmp_path / "mydb.db"


def test_resolution_none_defaults_to_hevy_db(tmp_path):
    assert _resolve_db_path(None, tmp_path) == tmp_path / "hevy.db"


def test_resolution_empty_string_defaults_to_hevy_db(tmp_path):
    assert _resolve_db_path("", tmp_path) == tmp_path / "hevy.db"
