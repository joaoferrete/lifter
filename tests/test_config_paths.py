"""Tests for path resolution in config.py, paths.py and fit/auth.py."""

from pathlib import Path

# ── live module values (sandboxed by LIFTER_HOME in conftest) ─────────────────


def test_db_path_respects_resolution_rules():
    import os

    import config
    import paths

    raw = os.environ.get("DB_PATH")
    if raw and Path(raw).is_absolute():
        # Absolute override must be honoured as-is
        assert Path(raw) == config.DB_PATH
    else:
        # Relative or unset → must resolve under the data dir
        assert config.DB_PATH.parent == paths.DATA_DIR


def test_credentials_file_defaults_to_config_dir(monkeypatch):
    import paths
    from fit.auth import credentials_file

    monkeypatch.delenv("GOOGLE_CREDENTIALS_FILE", raising=False)
    assert credentials_file() == paths.FIT_CREDENTIALS_FILE


def test_credentials_file_env_absolute(monkeypatch, tmp_path):
    from fit.auth import credentials_file

    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", str(tmp_path / "creds.json"))
    assert credentials_file() == tmp_path / "creds.json"


def test_credentials_file_env_relative_anchors_at_config_dir(monkeypatch):
    import paths
    from fit.auth import credentials_file

    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", "my-creds.json")
    assert credentials_file() == paths.CONFIG_DIR / "my-creds.json"


def test_credentials_file_prefers_profile_file(monkeypatch, tmp_path):
    import config
    from fit.auth import credentials_file

    monkeypatch.delenv("GOOGLE_CREDENTIALS_FILE", raising=False)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "hevy.db")
    profile_creds = tmp_path / "fit_credentials.json"
    profile_creds.write_text("{}")
    assert credentials_file() == profile_creds


def test_credentials_file_env_beats_profile_file(monkeypatch, tmp_path):
    import config
    from fit.auth import credentials_file

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "hevy.db")
    (tmp_path / "fit_credentials.json").write_text("{}")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", str(tmp_path / "env-creds.json"))
    assert credentials_file() == tmp_path / "env-creds.json"


def test_credentials_file_falls_back_to_global_without_profile_file(monkeypatch, tmp_path):
    import paths
    import config
    from fit.auth import credentials_file

    monkeypatch.delenv("GOOGLE_CREDENTIALS_FILE", raising=False)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "hevy.db")
    assert credentials_file() == paths.FIT_CREDENTIALS_FILE


def test_token_file_is_sibling_of_db():
    import config
    from fit.auth import _token_file

    assert _token_file().parent == config.DB_PATH.parent


# ── DB path resolution logic (config._resolve_db_path) ───────────────────────


def test_resolution_absolute_env_var(tmp_path):
    import config

    abs_path = tmp_path / "custom.db"
    assert config._resolve_db_path(str(abs_path)) == abs_path


def test_resolution_relative_env_var_uses_data_dir():
    import config
    import paths

    assert config._resolve_db_path("mydb.db") == paths.DATA_DIR / "mydb.db"


def test_resolution_none_defaults_to_hevy_db():
    import config
    import paths

    assert config._resolve_db_path(None) == paths.DATA_DIR / "hevy.db"


def test_resolution_empty_string_defaults_to_hevy_db():
    import config
    import paths

    assert config._resolve_db_path("") == paths.DATA_DIR / "hevy.db"
