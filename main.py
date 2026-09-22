#!/usr/bin/env python3
"""PSYGRID 10-instrument tournament engine — CLI entry point.

    python main.py                 start the live engine
    python main.py --demo          run against an offline synthetic feed +
                                    mocked Telegram (no network/credentials
                                    required)
    python main.py --once          run a single scan+tournament cycle then
                                    exit (combine with --demo for a quick
                                    end-to-end smoke test)
"""

from __future__ import annotations

import argparse
import sys
import time

from psygrid import demo_data
from psygrid.api_client import ApiError, RealMarketApiClient
from psygrid.config import ConfigError, load_config
from psygrid.engine import Engine
from psygrid.persistence import Persistence
from psygrid.telegram_client import TelegramClient

BANNER = "=" * 60


def test_api(api_client: RealMarketApiClient) -> bool:
    try:
        result = api_client.fetch()
    except ApiError as exc:
        print(f"[STARTUP] RealMarketAPI test FAILED: {exc}")
        return False
    status = "OK" if result.ok else "PARSED WITH ISSUES"
    print(f"[STARTUP] RealMarketAPI reachable — {len(result.instruments)} instrument(s) parsed ({status}).")
    for err in result.parse_errors:
        print(f"[STARTUP]   - {err}")
    return result.ok


def test_telegram(telegram_client: TelegramClient, configured: bool) -> bool:
    if not configured:
        print(
            "[STARTUP] Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
            "missing) — tournament reports will not be sent."
        )
        return False
    # Uses check_connectivity() (getMe + getChat) rather than send_message()
    # so a routine engine restart never spams the chat — it only proves both
    # secrets are valid and wired up correctly. Never prints the token or
    # chat id; a failure message is pre-sanitized by TelegramClient.
    result = telegram_client.check_connectivity()
    if result.ok:
        print("[STARTUP] Telegram test: OK (bot token and chat id verified).")
    else:
        print(f"[STARTUP] Telegram test FAILED: {result.error}")
    return result.ok


def check_telegram_only(config, demo: bool) -> int:
    """Dedicated, non-spammy connectivity check for CI / manual verification.

    Fails safely and clearly (no engine, no SQLite, no RealMarketAPI touched)
    if either secret is missing, and never prints a credential value.
    """
    try:
        if not demo:
            config.require_telegram_credentials()
    except ConfigError as exc:
        print(f"[CONFIG ERROR] {exc}")
        return 1

    _, telegram_client, _, configured = build_clients(config, demo)
    ok = test_telegram(telegram_client, configured or demo)
    return 0 if ok else 1


def build_clients(config, demo: bool):
    if demo:
        generator = demo_data.DemoFeedGenerator()
        api_client = RealMarketApiClient(config.api_url, config.api_timeout_seconds, fetch_fn=generator.next_payload)

        sent_messages = []

        def mock_post(url, payload):
            sent_messages.append(payload["text"])
            return {"ok": True, "result": {"message_id": len(sent_messages)}}

        def mock_get(url):
            return {"ok": True, "result": {}}

        telegram_client = TelegramClient(
            config.telegram_bot_token or "demo-token",
            config.telegram_chat_id or "demo-chat",
            post_fn=mock_post,
            get_fn=mock_get,
        )
        return api_client, telegram_client, sent_messages, True

    api_client = RealMarketApiClient(config.api_url, config.api_timeout_seconds)
    telegram_client = TelegramClient(config.telegram_bot_token, config.telegram_chat_id)
    return api_client, telegram_client, None, config.telegram_configured()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="PSYGRID 10-instrument tournament engine")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run against an offline synthetic feed with a mocked Telegram client (no network/credentials needed).",
    )
    parser.add_argument(
        "--once", action="store_true", help="Run a single scan + tournament cycle, print the result, then exit."
    )
    parser.add_argument("--db-path", default=None, help="Override PSYGRID_DB_PATH for this run.")
    parser.add_argument(
        "--check-telegram",
        action="store_true",
        help=(
            "Verify TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are present and valid, then exit. "
            "Sends no message (safe to run in CI on every push). Fails with a clear "
            "[CONFIG ERROR] if either secret is missing; never prints their values."
        ),
    )
    args = parser.parse_args(argv)

    print(BANNER)
    print("PSYGRID TOURNAMENT ENGINE — STARTUP")
    print(BANNER)

    config = load_config()
    if args.db_path:
        config.db_path = args.db_path

    if args.check_telegram:
        return check_telegram_only(config, args.demo)

    problems = config.validate()
    if problems:
        for p in problems:
            print(f"[CONFIG ERROR] {p}")
        return 1
    print("[STARTUP] 1/6 Configuration loaded and validated.")

    api_client, telegram_client, sent_messages, telegram_configured = build_clients(config, args.demo)
    if args.demo:
        print("[STARTUP]     DEMO mode active: synthetic feed, mocked Telegram, no live network calls.")

    print("[STARTUP] 2/6 Testing RealMarketAPI connectivity...")
    api_ok = test_api(api_client)

    print("[STARTUP] 3/6 Testing Telegram connectivity...")
    test_telegram(telegram_client, telegram_configured)

    print(f"[STARTUP] 4/6 Initializing SQLite at '{config.db_path}'...")
    persistence = Persistence(config.db_path)
    print("[STARTUP]     SQLite ready.")

    engine = Engine(config, api_client, persistence, telegram_client)
    print("[STARTUP] 5/6 Engine initialized.")

    if not api_ok and not args.demo:
        print(
            "[STARTUP] WARNING: RealMarketAPI was not reachable at startup. The engine will "
            "keep retrying on its normal scan interval and will report NO TRADE / DATA "
            "UNAVAILABLE rather than fabricate data."
        )

    print("[STARTUP] 6/6 Starting continuous scanning...")
    print(BANNER)

    try:
        if args.once:
            engine.scan_once()
            print(engine.render_status())
            print(BANNER)
            result = engine.run_tournament_cycle(int(engine.now_fn()))
            if sent_messages:
                print("[TELEGRAM MESSAGE SENT]")
                print(sent_messages[-1])
            persistence.close()
            return 0

        last_reported = 0
        while True:
            engine.tick()
            print(engine.render_status())
            print(BANNER)
            if sent_messages and engine.tournament_count > last_reported:
                # Surface every tournament report as it fires.
                print("[TELEGRAM MESSAGE SENT]")
                print(sent_messages[-1])
                print(BANNER)
                last_reported = engine.tournament_count
            time.sleep(config.scan_interval_seconds)
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Stopped by user.")
    finally:
        persistence.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
