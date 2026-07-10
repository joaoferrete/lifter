"""Tests for config.set_env_values (.env upsert writer) and reload_env."""

import stat

import config


def test_creates_file_with_0600(tmp_path):
    env = tmp_path / ".env"
    config.set_env_values({"GEMINI_API_KEY": "abc"}, env_file=env)
    assert env.read_text() == "GEMINI_API_KEY=abc\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_updates_existing_key_variants(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# comment stays\nGEMINI_API_KEY=old\nexport GROQ_API_KEY=old2\nAWS_REGION = us-east-1\nUNRELATED=keep\n"
    )
    config.set_env_values(
        {"GEMINI_API_KEY": "new", "GROQ_API_KEY": "new2", "AWS_REGION": "sa-east-1"},
        env_file=env,
    )
    text = env.read_text()
    assert "GEMINI_API_KEY=new\n" in text
    assert "GROQ_API_KEY=new2\n" in text
    assert "AWS_REGION=sa-east-1\n" in text
    assert "old" not in text
    assert "# comment stays\n" in text
    assert "UNRELATED=keep\n" in text


def test_commented_out_lines_are_preserved_and_key_appended(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# AI_MODEL=claude-sonnet-5\n")
    config.set_env_values({"AI_MODEL": "gemini-flash-latest"}, env_file=env)
    text = env.read_text()
    assert "# AI_MODEL=claude-sonnet-5\n" in text
    assert text.endswith("AI_MODEL=gemini-flash-latest\n")


def test_appends_missing_keys_and_handles_no_trailing_newline(tmp_path):
    env = tmp_path / ".env"
    env.write_text("EXISTING=1")  # no trailing newline
    config.set_env_values({"NEW_KEY": "v"}, env_file=env)
    assert env.read_text() == "EXISTING=1\nNEW_KEY=v\n"


def test_atomic_no_tmp_leftover(tmp_path):
    env = tmp_path / ".env"
    config.set_env_values({"A": "1"}, env_file=env)
    assert list(tmp_path.iterdir()) == [env]


def test_key_name_prefix_does_not_match(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AWS_REGION_BACKUP=x\n")
    config.set_env_values({"AWS_REGION": "eu-west-1"}, env_file=env)
    text = env.read_text()
    assert "AWS_REGION_BACKUP=x\n" in text
    assert "AWS_REGION=eu-west-1\n" in text
