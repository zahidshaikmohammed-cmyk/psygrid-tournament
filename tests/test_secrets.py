"""Tests for secret handling: env-var loading, fail-safe config errors, and
that a bot token can never leak into a log/error message.
"""

import os

import pytest

from psygrid.config import Config, ConfigError
from psygrid.telegram_client import TelegramClient


# -- Config: os.getenv-backed, fail-safe on missing secrets ------------------


def test_config_reads_telegram_secrets_from_environment(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123:token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "555")
    config = Config()
    assert config.telegram_bot_token == "abc123:token"
    assert config.telegram_chat_id == "555"


def test_config_defaults_telegram_secrets_to_empty_when_unset(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    config = Config()
    assert config.telegram_bot_token == ""
    assert config.telegram_chat_id == ""
    assert config.telegram_configured() is False


def test_require_telegram_credentials_raises_clear_error_when_both_missing():
    config = Config(telegram_bot_token="", telegram_chat_id="")
    with pytest.raises(ConfigError) as exc_info:
        config.require_telegram_credentials()
    message = str(exc_info.value)
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "TELEGRAM_CHAT_ID" in message


def test_require_telegram_credentials_names_only_the_missing_one():
    config = Config(telegram_bot_token="present-token", telegram_chat_id="")
    with pytest.raises(ConfigError) as exc_info:
        config.require_telegram_credentials()
    message = str(exc_info.value)
    assert "TELEGRAM_CHAT_ID" in message
    assert "TELEGRAM_BOT_TOKEN" not in message
    # The error must never echo the (present) secret's actual value back.
    assert "present-token" not in message


def test_require_telegram_credentials_passes_silently_when_both_present():
    config = Config(telegram_bot_token="tok", telegram_chat_id="chat")
    config.require_telegram_credentials()  # must not raise


# -- TelegramClient: never leaks the token, even on failure -------------------


SECRET_TOKEN = "123456789:AAExampleSuperSecretTokenValueDoNotLeak"


def test_check_connectivity_ok_never_includes_token_in_success_result():
    client = TelegramClient(
        SECRET_TOKEN,
        "chat-1",
        get_fn=lambda url: {"ok": True, "result": {}},
    )
    result = client.check_connectivity()
    assert result.ok
    assert result.error is None


def test_check_connectivity_failure_message_is_sanitized():
    def boom(url):
        # Simulate the very real failure mode where an HTTP client embeds
        # the full request URL (token and all) in its own exception text.
        raise ConnectionError(f"failed to connect to https://api.telegram.org/bot{SECRET_TOKEN}/getMe")

    client = TelegramClient(SECRET_TOKEN, "chat-1", get_fn=boom)
    result = client.check_connectivity()
    assert not result.ok
    assert SECRET_TOKEN not in result.error
    assert "REDACTED" in result.error


def test_send_message_failure_message_is_sanitized():
    def boom(url, payload):
        raise ConnectionError(f"connection refused for url including bot{SECRET_TOKEN}")

    client = TelegramClient(SECRET_TOKEN, "chat-1", post_fn=boom)
    result = client.send_message("hello")
    assert not result.ok
    assert SECRET_TOKEN not in result.error


def test_check_connectivity_fails_cleanly_without_crashing_when_unconfigured():
    client = TelegramClient("", "", get_fn=lambda url: {"ok": True})
    result = client.check_connectivity()
    assert not result.ok
    assert "not configured" in result.error


def test_check_connectivity_never_sends_a_message():
    post_calls = []

    def post_fn(url, payload):
        post_calls.append((url, payload))
        return {"ok": True}

    get_calls = []

    def get_fn(url):
        get_calls.append(url)
        return {"ok": True, "result": {}}

    client = TelegramClient("token", "chat", post_fn=post_fn, get_fn=get_fn)
    result = client.check_connectivity()
    assert result.ok
    assert post_calls == []  # never used sendMessage
    assert len(get_calls) == 2  # getMe + getChat


# -- main.py CLI: --check-telegram fails safely and never prints secrets ------


def test_cli_check_telegram_fails_with_config_error_when_missing(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    import main as main_module

    config = Config(telegram_bot_token="", telegram_chat_id="")
    exit_code = main_module.check_telegram_only(config, demo=False)
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[CONFIG ERROR]" in captured.out
    assert "TELEGRAM_BOT_TOKEN" in captured.out


def test_cli_check_telegram_succeeds_in_demo_mode_with_no_network(capsys):
    import main as main_module

    config = Config(telegram_bot_token="", telegram_chat_id="")
    exit_code = main_module.check_telegram_only(config, demo=True)
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "OK" in captured.out


def test_cli_check_telegram_never_prints_the_token(monkeypatch, capsys):
    import main as main_module
    from psygrid import telegram_client as telegram_client_module

    class _FakeRequests:
        @staticmethod
        def get(url, timeout=10):
            # Simulate a real HTTP client's connection-error text, which
            # typically embeds the full request URL — token included — with
            # no real network call made.
            raise ConnectionError(f"simulated connection failure for {url}")

    monkeypatch.setattr(telegram_client_module, "requests", _FakeRequests)

    config = Config(telegram_bot_token=SECRET_TOKEN, telegram_chat_id="chat-1")
    main_module.check_telegram_only(config, demo=False)
    captured = capsys.readouterr()
    assert SECRET_TOKEN not in captured.out
    assert SECRET_TOKEN not in captured.err
