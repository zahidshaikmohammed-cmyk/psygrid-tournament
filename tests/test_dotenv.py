"""Tests for local-development `.env` loading (psygrid/config.py).

Covers: values in `.env` reach `os.environ` (and then `Config`), a
shell/CI-supplied environment variable always wins over `.env`, a missing
or unreadable `.env` is handled gracefully (no crash), and nothing from the
file is ever printed.
"""

from __future__ import annotations

import os

from psygrid.config import DEFAULT_DOTENV_PATH, Config, load_config, load_env_file

SECRET_VALUE = "abc123:DotenvLoadedSecretDoNotPrint"


def _write_env_file(tmp_path, content: str):
    env_file = tmp_path / ".env"
    env_file.write_text(content)
    return env_file


def test_load_env_file_sets_a_previously_unset_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("PSYGRID_TEST_DOTENV_A", raising=False)
    env_file = _write_env_file(tmp_path, "PSYGRID_TEST_DOTENV_A=hello-from-dotenv\n")
    try:
        loaded = load_env_file(path=env_file)
        assert loaded is True
        assert os.environ["PSYGRID_TEST_DOTENV_A"] == "hello-from-dotenv"
    finally:
        os.environ.pop("PSYGRID_TEST_DOTENV_A", None)


def test_config_reads_a_value_that_came_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    env_file = _write_env_file(
        tmp_path,
        f"TELEGRAM_BOT_TOKEN={SECRET_VALUE}\nTELEGRAM_CHAT_ID=555\n",
    )
    try:
        load_env_file(path=env_file)
        config = Config()
        assert config.telegram_bot_token == SECRET_VALUE
        assert config.telegram_chat_id == "555"
    finally:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)


def test_shell_or_ci_environment_variable_always_wins_over_dotenv(tmp_path, monkeypatch):
    # Simulates GitHub Actions: the variable is already set in the process
    # environment (as `env:` mapped from `secrets.*` would do) before any
    # .env file is considered.
    monkeypatch.setenv("PSYGRID_TEST_DOTENV_B", "set-by-shell-or-ci")
    env_file = _write_env_file(tmp_path, "PSYGRID_TEST_DOTENV_B=set-by-dotenv-should-not-win\n")

    loaded = load_env_file(path=env_file)  # override=False by default

    assert loaded is True  # the file WAS read...
    # ...but the pre-existing value must be completely untouched.
    assert os.environ["PSYGRID_TEST_DOTENV_B"] == "set-by-shell-or-ci"


def test_telegram_secrets_from_ci_env_are_not_overridden_by_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "ci-supplied-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "ci-supplied-chat")
    env_file = _write_env_file(
        tmp_path,
        "TELEGRAM_BOT_TOKEN=dotenv-token-must-lose\nTELEGRAM_CHAT_ID=dotenv-chat-must-lose\n",
    )

    load_env_file(path=env_file)
    config = Config()

    assert config.telegram_bot_token == "ci-supplied-token"
    assert config.telegram_chat_id == "ci-supplied-chat"


def test_missing_dotenv_file_is_handled_gracefully(tmp_path):
    missing = tmp_path / "does-not-exist" / ".env"
    loaded = load_env_file(path=missing)
    assert loaded is False  # no exception, no crash


def test_default_dotenv_path_is_resolved_from_module_location_not_cwd(tmp_path, monkeypatch):
    # .env resolution must not depend on the working directory the engine
    # happens to be launched from — it's always <repo root>/.env.
    monkeypatch.chdir(tmp_path)
    assert DEFAULT_DOTENV_PATH.name == ".env"
    assert DEFAULT_DOTENV_PATH.parent.name == "psygrid-tournament" or (
        DEFAULT_DOTENV_PATH.parent / "psygrid" / "config.py"
    ).exists()


def test_load_config_never_crashes_when_no_dotenv_file_exists(monkeypatch):
    # The checked-out repo has no committed .env (it's gitignored); this
    # must be a normal, silent no-op rather than a startup failure.
    config = load_config()
    assert isinstance(config, Config)


def test_dotenv_values_are_never_printed(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env_file = _write_env_file(tmp_path, f"TELEGRAM_BOT_TOKEN={SECRET_VALUE}\n")
    try:
        load_env_file(path=env_file)
        captured = capsys.readouterr()
        assert SECRET_VALUE not in captured.out
        assert SECRET_VALUE not in captured.err
    finally:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)


def test_dotenv_override_true_is_opt_in_only():
    # Sanity check on the underlying contract this module relies on: this
    # codebase never passes override=True, but prove the parameter behaves
    # as documented so a future accidental flip would be caught here too.
    import os as _os
    import tempfile

    _os.environ["PSYGRID_TEST_DOTENV_C"] = "original"
    try:
        with tempfile.TemporaryDirectory() as d:
            from pathlib import Path

            env_file = Path(d) / ".env"
            env_file.write_text("PSYGRID_TEST_DOTENV_C=overridden\n")
            load_env_file(path=env_file, override=True)
            assert _os.environ["PSYGRID_TEST_DOTENV_C"] == "overridden"
    finally:
        _os.environ.pop("PSYGRID_TEST_DOTENV_C", None)
