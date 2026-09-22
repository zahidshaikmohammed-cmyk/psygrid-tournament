# PSYGRID — 10-Instrument Tournament Engine

A standalone engine that continuously analyzes 10 instruments from
RealMarketAPI in parallel, runs a formal "tournament" every 30 minutes, and
selects **one best trade candidate or NO TRADE** — never a forced winner.
It is decision-support only: **V1 places no orders.**

This engine is completely independent of Psygnal/Psync. It imports nothing
from, and shares no code with, that codebase.

## Philosophy

One shared analytical framework (`psygrid/instrument_analysis.py`) is run
identically across every instrument — there are no per-instrument special
cases, no hard-coded favorites, and no fixed rankings. Every score is an
explainable, component-by-component number, not a black box, and is never
called a "probability" unless it has actually been calibrated against real
recorded outcomes (see Historical Validation below). Higher timeframes are
never trusted from the provider — they are derived from genuine M1 candles
and tagged `synthetic_from_m1=True`.

## How it works

1. **Continuous scan loop** (default every 20s): fetch `m1-live.json`,
   validate it, merge new candles into a rolling per-instrument M1 store
   (never fabricating a missing candle), derive M5/M15/M30/H1, and
   recompute the full analytical state for all 10 instruments in parallel.
2. **Every 30 minutes** (UTC/IST :00 and :30 marks — IST's +5:30 offset
   keeps both grids aligned): freeze a snapshot, apply hard disqualification
   gates, score the survivors on 8 explainable components, and select
   either one winner (score ≥ `PSYGRID_MIN_QUALITY_SCORE`) or **NO TRADE**.
3. Send **exactly one** Telegram report per tournament cycle.
4. Persist everything — candles, states, setups, tournament results, and
   forward-tracked outcomes (MFE/MAE, 1/5/10/15/30-minute results) — to
   SQLite for future statistical research into what actually has positive
   expectancy.

## Historical validation, not assumption

Every recorded candidate is watched forward and scored against real,
subsequent M1 candles (`psygrid/outcomes.py`). The "historical conditional
quality" score component starts neutral (50, `calibrated=False`) and only
moves once at least `PSYGRID_HISTORICAL_MIN_SAMPLE` real resolved outcomes
exist for that instrument/setup/regime combination. Nothing is assumed
profitable up front.

## The confirmed live schema

`psygrid/api_client.py` parses the ACTUAL live schema, captured from a real
`m1-live.json` response:

```json
{
  "schema_version": "1.0", "service": "psygrid-forex", "provider": "realmarketapi",
  "timeframe": "M1", "candle_source": "provider_native", "synthetic_candles": false,
  "generated_at": "...", "status": "ok", "universe_size": 10,
  "symbols": {
    "<SYMBOL>": {
      "symbol": "<SYMBOL>", "market_state": "open", "status": "ok",
      "last_candle_timestamp": "...", "candle_count": 0, "gap_recoveries": 0, "rejected_count": 0,
      "candles": [
        {"timestamp": "...", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0, "bid": null, "ask": null}
      ]
    }
  }
}
```

`payload["symbols"]` is the authoritative instrument universe — nothing
outside it is ever treated as market data (an earlier version of this
adapter guessed at the schema before a real response was available, and
that guess was wrong: it treated top-level metadata scalars like
`schema_version`/`service`/`timeframe` as if they were zero-candle
instrument entries, which is exactly what produced a misleading
`Instruments: 8` on a real run rather than a real 8-of-10 figure. That
guessing logic has been replaced entirely — see `tests/test_api_client.py`
for `test_all_ten_symbols_parse_correctly` and friends).

Two categories of validation apply, deliberately kept separate:

- **FATAL** (raises `ApiError`, the whole fetch is discarded — same
  handling as a network failure): `timeframe != "M1"`,
  `candle_source != "provider_native"`, `synthetic_candles != false`, or
  top-level `status != "ok"`. Any of these mean the payload cannot be
  trusted as genuine, non-synthetic, provider-native M1 data at all.
- **NON-FATAL / reported** (whatever's valid is still used):
  `universe_size` below what's expected, or an individual symbol whose
  `candles` array is missing/empty/entirely malformed. The exact symbol
  name is recorded (`FetchResult.rejected_symbols`) wherever the payload
  named it at all — the engine also remembers every symbol name it has
  ever seen live so it can name one that disappears from the payload
  *entirely* on a later fetch, not just one that's present-but-broken.
  `Engine.render_status()` surfaces this honestly (`Instruments: 8/10
  usable`, `Coverage: PARTIAL`, `Rejected by provider: SYM_A, SYM_B`)
  rather than a gate being loosened to make the screen say `LIVE`/`10/10`
  when it isn't.

Per-symbol metadata (`status`, `market_state`, `gap_recoveries`,
`rejected_count`) is preserved and folded into
`psygrid/data_quality.py`'s usability/quality determination, not just
carried along for display — a symbol whose own provider-reported `status`
isn't `"ok"` is marked not usable even if its OHLCV data looks fine in
isolation.

## Raw data vs. engine-derived features (and indicator policy)

**RealMarketAPI's contract is OHLCV + timestamp only.** It provides no
RSI, EMA, MACD, ATR, VWAP, Bollinger Bands, or any other indicator.
`psygrid/api_client.py` parses the CONFIRMED live schema (captured from a
real response — see *The confirmed live schema* below): six fields per
candle (`timestamp`/`open`/`high`/`low`/`close`/`volume`), plus `bid`/`ask`
which are present but never read (they're `null` in practice and the
OHLCV-only strategy has no use for them — a null `bid`/`ask` is never a
reason to reject a candle). Anything else present in a payload (an
indicator field a future provider version might add) is parsed out and
discarded, never stored on a `Candle` or used anywhere downstream — `Candle`
is a fixed-field dataclass with no slot an extra value could ride along in
(verified in `tests/test_api_client.py`).

Two clearly separated categories exist everywhere in this codebase:

- **RAW** — genuine M1 OHLCV candles as received from RealMarketAPI
  (`Candle.synthetic_from_m1 == False`). This is the only authoritative
  market input; nothing overrides or second-guesses it.
- **DERIVED** — everything else, computed locally, always from raw OHLCV
  (directly, or from other derived values that trace back to it):
  - M5/M15/M30/H1 candles (`psygrid/timeframes.py`,
    `Candle.synthetic_from_m1 == True`) — never trusted from the provider
    even if it were to start supplying them.
  - Structure, momentum, volatility, liquidity, price-behaviour, setup,
    execution, and time-behaviour features (`psygrid/structure.py`,
    `momentum.py`, `volatility.py`, `liquidity.py`, `price_behavior.py`,
    `setups.py`, `execution.py`, `time_behavior.py`) — each module's
    docstring is tagged `DERIVED FEATURE` and states exactly what raw
    inputs it's computed from.

**Indicators are the minority, not the default.** The engine is built
structure/momentum/volatility/liquidity-first, per the spec's own
philosophy — it does not reach for a conventional indicator stack. The one
classic "indicator" used anywhere is **ATR** (Average True Range,
`psygrid/volatility.py`), computed locally from raw high/low/close, because
stop-sizing and R:R (`execution.py`) are structurally meaningless without
some volatility measure — it's there because it's load-bearing, not because
it's conventional. Momentum is measured as plain rate-of-change on raw
closes rather than RSI/EMA/MACD, explicitly to keep it auditable
(`momentum.py`'s own docstring explains this choice).

**Predictive value is tested, not assumed**, for the same reason as any
setup: every candidate a tournament selects is watched forward and its
real 1/5/10/15/30-minute outcomes are recorded (see *Historical validation*
above). The "historical conditional quality" score component is how ATR-
and momentum-informed setups actually get judged — by realized outcome, not
by indicator folklore — and it stays neutral until enough real samples
exist.

**History is preserved for every feature's widest lookback.**
`PSYGRID_HISTORY_MIN_CANDLES` (default 200) is validated at startup
(`Config.validate()`) to never fall below `MIN_RELIABLE_HISTORY_CANDLES`
(120 — `psygrid/structure.py`'s swing-detection lookback, the widest window
any feature needs), and `PSYGRID_MAX_ROLLING_CANDLES` (default 1600, at
least the ~1,500 M1 candles the endpoint provides) is validated to never
fall below it either — so no feature can silently be starved of history by
a misconfigured environment variable.

See `tests/test_indicator_calculations.py` for hand-computed-value tests
of every derived feature (ATR/true range, momentum ROC, swing detection,
timeframe aggregation, liquidity extremes) against known OHLCV fixtures.

## File tree

```
psygrid-tournament/
├── main.py                      CLI entry point
├── requirements.txt
├── .env.example                 every configurable env var, no secrets
├── .gitignore                   keeps .env, *.sqlite3, caches out of git
├── .github/
│   └── workflows/
│       └── ci.yml                pytest job + Telegram connectivity job
├── psygrid/
│   ├── config.py                env-var driven configuration + validation
│   ├── api_client.py            RealMarketAPI fetch, confirmed live-schema parsing
│   ├── candle_store.py          rolling per-instrument M1 state, continuity
│   ├── timeframes.py            genuine M1 -> M5/M15/M30/H1 aggregation
│   ├── data_quality.py          freshness / continuity / history checks
│   ├── structure.py             swings, HH/HL/LH/LL, breaks, consolidation
│   ├── momentum.py               direction, acceleration, exhaustion
│   ├── volatility.py             ATR, relative vol, expansion/compression
│   ├── liquidity.py              extremes, equal highs/lows, sweeps
│   ├── price_behavior.py         impulse/pullback/breakout classification
│   ├── time_behavior.py          session, setup age, travel distance
│   ├── setups.py                 shared setup-detection rule set
│   ├── execution.py              R:R, stop distance, slippage sensitivity
│   ├── instrument_analysis.py    orchestrates the above per instrument
│   ├── scoring.py                hard gates + explainable component scoring
│   ├── tournament.py             the 30-minute, 10-instrument tournament
│   ├── scheduler.py              30-minute boundary clock, restart-safe
│   ├── outcomes.py                forward MFE/MAE/horizon outcome tracking
│   ├── persistence.py             SQLite schema + DAO
│   ├── telegram_client.py         Telegram Bot API + message formatting
│   ├── engine.py                  orchestrator: scan loop + tournaments
│   └── demo_data.py               offline synthetic feed for --demo
└── tests/
    ├── fixtures.py                deterministic synthetic candle builders
    ├── test_secrets.py             secret loading, fail-safe errors, redaction
    ├── test_dotenv.py               .env loading, shell/CI precedence
    ├── test_indicator_calculations.py  hand-computed ATR/ROC/swings/aggregation
    └── test_*.py                   ~155 tests across every module
```

## Configuring environment variables

Nothing is hard-coded. For local development, copy `.env.example` to `.env`
and fill in credentials — `psygrid/config.py` loads it automatically via
[python-dotenv](https://pypi.org/project/python-dotenv/) the moment it's
imported, so no `export`/`source` step is required:

```bash
cp .env.example .env
# edit .env with real values, then just run:
python main.py
```

`.env` is a local-dev convenience only. It **never** overrides a variable
already present in the environment (`load_dotenv(..., override=False)`),
so anything set by your shell, or — in CI — by GitHub Actions mapping
`secrets.*` to `env:`, always takes precedence; `.env` only fills in gaps.
`.env` is listed in `.gitignore` and must never be committed. See
`tests/test_dotenv.py` for tests proving this precedence.

You can still export variables directly instead of using `.env`:

```bash
# minimum required for live Telegram alerts:
export TELEGRAM_BOT_TOKEN="123456:ABC-your-bot-token"
export TELEGRAM_CHAT_ID="123456789"

# optional overrides (defaults shown in .env.example), e.g.:
export REALMARKET_API_URL="http://140.245.226.102:8080/public/m1-live.json"
export PSYGRID_MIN_QUALITY_SCORE=78
```

## Running the engine

```bash
pip install -r requirements.txt

# Live engine (requires network access to RealMarketAPI and Telegram):
python main.py

# Offline demo — synthetic 10-instrument feed + mocked Telegram, no
# network or credentials required:
python main.py --demo

# Single scan + tournament cycle then exit (useful for smoke-testing):
python main.py --demo --once
python main.py --once
```

`python main.py` performs, in order: load+validate config, test
RealMarketAPI connectivity, test Telegram connectivity, initialize SQLite,
then starts the continuous scan loop with a live terminal status display,
running a tournament on every 30-minute boundary until you stop it with
Ctrl-C.

## Telegram secrets: how they're wired

- **Reading**: `psygrid/config.py` reads `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_CHAT_ID` exclusively via `os.getenv(...)`. Neither has a
  non-empty default and neither is ever hard-coded anywhere in this
  codebase.
- **Fail-safe on missing secrets**: `Config.require_telegram_credentials()`
  raises a `ConfigError` that names exactly which variable(s) are missing —
  never a bare `KeyError`/`TypeError`/`None`-concatenation crash, and never
  a value. It's used by `python main.py --check-telegram` (see below). The
  main engine (`python main.py`) still starts and runs without Telegram
  configured — alerts are simply disabled and it says so — since the
  10-instrument analysis itself doesn't depend on Telegram.
- **Never logged**: `TelegramClient._sanitize()` strips the bot token out of
  every error string before it can reach a log line, a returned
  `SendResult.error`, or a printed message — this matters because HTTP
  client libraries often embed the full request URL (token included) in
  their own connection-error text. `check_connectivity()` (see below)
  never includes either secret's value in its result, success or failure.
- **Local development**: copy `.env.example` to `.env` (already covered
  above); `.env` is listed in `.gitignore` and must never be committed.
- **GitHub Actions**: add both secrets under this repository's *Settings →
  Secrets and variables → Actions*, named exactly `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_CHAT_ID`. `.github/workflows/ci.yml`'s `telegram-connectivity`
  job maps them to environment variables via `${{ secrets.* }}` — never as
  command-line arguments — and only for that job (least privilege). That
  job intentionally never runs on `pull_request` events, since GitHub does
  not expose secrets to forked-repo PRs and the job would otherwise fail
  with a misleading error unrelated to the PR's actual contents.

### Connectivity test

```bash
python main.py --check-telegram          # live: needs both secrets set
python main.py --demo --check-telegram   # offline: no network, no secrets
```

This is deliberately **not** the same as sending a real tournament report:
it calls Telegram's read-only `getMe` (proves the bot token is valid) and
`getChat` (proves the chat id is valid and reachable by the bot) — so it's
safe to run on every push/CI run without spamming the chat. It exits `1`
with a `[CONFIG ERROR]` line if either secret is absent, exits `1` with a
sanitized failure reason if the secrets are present but invalid/unreachable,
and exits `0` on success. It never prints either secret's value in any of
these paths (see `tests/test_secrets.py`).

## Testing

```bash
pip install -r requirements.txt
python -m pytest tests/ -q
```

155 deterministic tests cover: API parsing (including malformed/partial
payloads), stale/missing/misaligned candle data, M5/M15/M30/H1 aggregation
correctness (including that no bucket is ever fabricated), structure/setup
detection, hard-gate disqualification, candidate ranking and deterministic
tie-breaking, the NO-TRADE path, tournament scheduler synchronization
(exactly one tournament per 30-minute boundary, including under restart
recovery), Telegram message formatting and failure handling, SQLite
persistence, forward outcome tracking, and secret handling (env-var
loading, fail-safe missing-credential errors, and that a bot token can
never leak into a log/error/CLI-output string). No randomness is used
anywhere in the suite — every fixture is an explicit, reproducible formula.

## Verification performed before calling this done

1. `python -m pytest tests/ -q` → **155 passed**.
2. `python main.py --demo --once` → all 10 synthetic instruments analyzed
   in parallel, a full tournament ran, and exactly one Telegram message was
   generated (verified with `PSYGRID_MIN_QUALITY_SCORE` at both its default
   and a lowered value, to exercise both the **NO TRADE** and **WINNER**
   message paths).
3. `python main.py --demo` run for several ticks under a short scan
   interval → confirmed the tournament fires exactly once per 30-minute
   boundary and the terminal status view updates every scan tick.
4. The adapter (`psygrid/api_client.py`) is now written and tested against
   the CONFIRMED live schema (a real `m1-live.json` response was captured
   and used to build `tests/fixtures.py`'s `live_payload_json` — see *The
   confirmed live schema* above), including a regression test proving the
   earlier schema guess's exact failure mode (`Instruments: 8` from
   metadata being misparsed as zero-candle instruments) can no longer
   happen (`test_missing_symbols_key_raises_api_error` and friends).
   Literal network reachability of `140.245.226.102:8080` was not
   re-verified from this sandbox — that IP is not routable from this
   container at the network layer, independent of this codebase.
   `main.py`'s startup step 2/6 performs this check for real the first
   time you run it in an environment with normal internet access, and
   prints exactly how many symbols parsed and whether coverage is full.

## Honest limitations (V1)

- No broker/order execution of any kind — analysis and alerting only.
- Scoring weights and thresholds are declared, explainable defaults, not
  statistically calibrated probabilities — they are labelled "scores"
  throughout the code and messages, deliberately.
- Session/session-overlap boundaries are an approximate UTC banding, not
  sourced from any calendar feed.
- No economic-event blackout is implemented (no event-calendar data source
  was provided); this is a known, explicitly-named gap rather than a
  silently-skipped one.
- Passing tests demonstrate the engine behaves as designed — they are not
  evidence of a trading edge, and this codebase makes no such claim.
