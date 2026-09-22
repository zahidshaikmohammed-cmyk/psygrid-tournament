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

## File tree

```
psygrid-tournament/
├── main.py                      CLI entry point
├── requirements.txt
├── .env.example                 every configurable env var, no secrets
├── psygrid/
│   ├── config.py                env-var driven configuration + validation
│   ├── api_client.py            RealMarketAPI fetch + defensive parsing
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
    └── test_*.py                  ~83 tests across every module
```

## Configuring environment variables

Nothing is hard-coded. Copy `.env.example` and fill in credentials, or
export directly:

```bash
cp .env.example .env
# edit .env, then:
export $(grep -v '^#' .env | xargs)

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

## Testing

```bash
pip install -r requirements.txt
python -m pytest tests/ -q
```

83 deterministic tests cover: API parsing (including malformed/partial
payloads), stale/missing/misaligned candle data, M5/M15/M30/H1 aggregation
correctness (including that no bucket is ever fabricated), structure/setup
detection, hard-gate disqualification, candidate ranking and deterministic
tie-breaking, the NO-TRADE path, tournament scheduler synchronization
(exactly one tournament per 30-minute boundary, including under restart
recovery), Telegram message formatting and failure handling, SQLite
persistence, and forward outcome tracking. No randomness is used anywhere
in the suite — every fixture is an explicit, reproducible formula.

## Verification performed before calling this done

1. `python -m pytest tests/ -q` → **83 passed**.
2. `python main.py --demo --once` → all 10 synthetic instruments analyzed
   in parallel, a full tournament ran, and exactly one Telegram message was
   generated (verified with `PSYGRID_MIN_QUALITY_SCORE` at both its default
   and a lowered value, to exercise both the **NO TRADE** and **WINNER**
   message paths).
3. `python main.py --demo` run for several ticks under a short scan
   interval → confirmed the tournament fires exactly once per 30-minute
   boundary and the terminal status view updates every scan tick.
4. **Live RealMarketAPI connectivity was not verified from this development
   sandbox** — outbound requests to the given endpoint's raw IP
   (`140.245.226.102:8080`) are not routable from this container (they time
   out at the network layer, independent of this codebase). `main.py`'s
   startup step 2/6 will perform this check for real the first time you run
   it in an environment with normal internet access, and will clearly print
   whether RealMarketAPI was reachable. If the live payload's field names
   differ from the aliases already handled in `psygrid/api_client.py`
   (`TIME_KEYS`/`OPEN_KEYS`/`HIGH_KEYS`/`LOW_KEYS`/`CLOSE_KEYS`/`VOLUME_KEYS`/
   `CANDLES_KEYS`), extend those tuples — nothing else in the engine needs
   to change.

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
