"""Tests for per-profile AI provider/model overrides (config.apply_ai_overrides)."""
import config


def _snapshot_env(monkeypatch, provider="gemini", model="gemini-flash-latest"):
    monkeypatch.setattr(config, "_ENV_AI_PROVIDER", provider)
    monkeypatch.setattr(config, "_ENV_AI_MODEL", model)
    monkeypatch.setattr(config, "AI_PROVIDER", provider)
    monkeypatch.setattr(config, "AI_MODEL", model)


def test_prefs_override_provider_and_model(tmp_db, monkeypatch):
    from db.goals import set_pref
    _snapshot_env(monkeypatch)
    set_pref("ai_provider", "claude")
    set_pref("ai_model", "claude-sonnet-5")

    config.apply_ai_overrides()

    assert config.AI_PROVIDER == "claude"
    assert config.AI_MODEL == "claude-sonnet-5"


def test_provider_pref_without_model_falls_to_provider_default(tmp_db, monkeypatch):
    from db.goals import set_pref
    _snapshot_env(monkeypatch, provider="gemini", model="gemini-2.5-pro")
    set_pref("ai_provider", "groq")

    config.apply_ai_overrides()

    assert config.AI_PROVIDER == "groq"
    # not the env model (which was chosen for gemini) — groq's own default
    assert config.AI_MODEL == config.default_model_for("groq")


def test_unknown_provider_pref_keeps_env(tmp_db, monkeypatch):
    from db.goals import set_pref
    _snapshot_env(monkeypatch)
    set_pref("ai_provider", "not-a-provider")

    config.apply_ai_overrides()

    assert config.AI_PROVIDER == "gemini"
    assert config.AI_MODEL == "gemini-flash-latest"


def test_cleared_prefs_restore_env(tmp_db, monkeypatch):
    from db.goals import set_pref
    _snapshot_env(monkeypatch)
    set_pref("ai_provider", "claude")
    config.apply_ai_overrides()
    assert config.AI_PROVIDER == "claude"

    set_pref("ai_provider", "")
    set_pref("ai_model", "")
    config.apply_ai_overrides()

    assert config.AI_PROVIDER == "gemini"
    assert config.AI_MODEL == "gemini-flash-latest"


def test_overrides_sync_provider_module(tmp_db, monkeypatch):
    import ai.provider as provider_mod
    from db.goals import set_pref
    _snapshot_env(monkeypatch)
    monkeypatch.setattr(provider_mod, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(provider_mod, "AI_MODEL", "gemini-flash-latest")
    set_pref("ai_provider", "claude")
    set_pref("ai_model", "claude-sonnet-5")

    config.apply_ai_overrides()

    assert provider_mod.AI_PROVIDER == "claude"
    assert provider_mod.AI_MODEL == "claude-sonnet-5"


def test_get_provider_api_key_explicit_arg(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "gm-test")
    monkeypatch.setattr(config, "AI_PROVIDER", "gemini")

    assert config.get_provider_api_key("claude") == "sk-ant-test"
    assert config.get_provider_api_key() == "gm-test"
    assert config.get_provider_api_key("bedrock") == ""


def test_set_env_values_plus_reload_env_updates_keys(tmp_db, monkeypatch, tmp_path):
    import paths
    env = tmp_path / ".env"
    monkeypatch.setattr(paths, "ENV_FILE", env)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    config.set_env_values({"GROQ_API_KEY": "gsk_fresh"})
    config.reload_env()

    assert config.GROQ_API_KEY == "gsk_fresh"
    assert config.get_provider_api_key("groq") == "gsk_fresh"


def test_reload_env_syncs_provider_module(tmp_db, monkeypatch, tmp_path):
    import paths
    import ai.provider as provider_mod
    env = tmp_path / ".env"
    monkeypatch.setattr(paths, "ENV_FILE", env)
    monkeypatch.setattr(provider_mod, "GEMINI_API_KEY", "stale", raising=False)

    config.set_env_values({"GEMINI_API_KEY": "gm_new"})
    config.reload_env()

    assert provider_mod.GEMINI_API_KEY == "gm_new"


def test_profile_prefs_still_win_after_reload(tmp_db, monkeypatch, tmp_path):
    import paths
    from db.goals import set_pref
    env = tmp_path / ".env"
    monkeypatch.setattr(paths, "ENV_FILE", env)
    # snapshot env + config attrs so reload_env's os.environ writes are undone
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    _snapshot_env(monkeypatch)
    set_pref("ai_provider", "claude")

    config.set_env_values({"AI_PROVIDER": "groq"})
    config.reload_env()
    config.apply_ai_overrides()

    assert config.AI_PROVIDER == "claude"   # profile pref beats fresh env
