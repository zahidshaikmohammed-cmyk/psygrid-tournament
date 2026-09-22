"""RealMarketAPI client.

Parses the CONFIRMED live schema of ``m1-live.json`` (verified against a
real response captured from the provider):

    {
      "schema_version": "1.0",
      "service": "psygrid-forex",
      "provider": "realmarketapi",
      "timeframe": "M1",
      "candle_source": "provider_native",
      "synthetic_candles": false,
      "generated_at": "...",
      "status": "ok",
      "universe_size": 10,
      "symbols": {
          "<SYMBOL>": {
              "symbol": "<SYMBOL>",
              "market_state": "open",
              "status": "ok",
              "last_candle_timestamp": "...",
              "updated_at": "...",
              "websocket_connected": true,
              "reconnect_count": ...,
              "gap_recoveries": ...,
              "rejected_count": ...,
              "candles_1m": [
                  {"timestamp": "...", "open": ..., "high": ..., "low": ...,
                   "close": ..., "volume": ..., "bid": null, "ask": null}
              ],
              "m1_valid": true
          }
      }
    }

``payload["symbols"]`` is the authoritative instrument universe — nothing
outside it is ever treated as market data. Each symbol's authoritative M1
candle array is at ``entry["candles_1m"]`` — this is the CONFIRMED field
name; two earlier guesses were both wrong and have both been corrected:
``"candles"`` does not exist in the real payload, and ``"candles_l1"`` was
itself a misread of the live response, not the real key either. Neither
is required or read. ``bid``/``ask`` are read from neither the payload nor
stored on :class:`~psygrid.models.Candle`: the OHLCV-only strategy has no
use for them, and their being ``null`` is never a reason to reject an
otherwise-valid candle.

NEVER fabricates data: any candle that fails validation is dropped and
recorded as a data-quality issue, never guessed or interpolated. Never
synthesizes a candle here — M5/M15/M30/H1 derivation happens strictly
downstream, in :mod:`psygrid.timeframes`, from genuine M1 candles only.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

from .models import Candle

# "timestamp" is the confirmed live field name and is tried first; the
# others remain as tolerant fallbacks in case of minor provider variation.
TIME_KEYS = ("timestamp", "time", "t", "ts", "datetime", "date")
OPEN_KEYS = ("open", "o")
HIGH_KEYS = ("high", "h")
LOW_KEYS = ("low", "l")
CLOSE_KEYS = ("close", "c")
VOLUME_KEYS = ("volume", "vol", "v", "tick_volume")

REQUIRED_TIMEFRAME = "M1"
REQUIRED_CANDLE_SOURCE = "provider_native"
REQUIRED_STATUS = "ok"


class ApiError(Exception):
    """Raised for any problem talking to / parsing RealMarketAPI."""


@dataclass
class ProviderMeta:
    """Top-level payload metadata, preserved verbatim for data-quality use."""

    schema_version: Optional[str]
    service: Optional[str]
    provider: Optional[str]
    timeframe: Optional[str]
    candle_source: Optional[str]
    synthetic_candles: Optional[bool]
    generated_at: Optional[str]
    status: Optional[str]
    universe_size: Optional[int]


@dataclass
class SymbolMeta:
    """Per-symbol metadata, preserved verbatim for data-quality use."""

    symbol: str
    market_state: Optional[str]
    status: Optional[str]
    last_candle_timestamp: Optional[str]
    candle_count: Optional[int]
    gap_recoveries: Optional[int]
    rejected_count: Optional[int]


@dataclass
class FetchResult:
    ok: bool
    fetched_at: int
    raw_bytes: int
    instruments: Dict[str, List[Candle]]  # symbol -> candles, only symbols with >=1 valid candle
    server_time: Optional[int]
    parse_errors: list
    skipped_candles: Dict[str, int]  # symbol -> count of dropped/invalid candles
    provider_meta: ProviderMeta
    symbol_meta: Dict[str, SymbolMeta] = field(default_factory=dict)  # every symbol key seen, valid or not
    rejected_symbols: Dict[str, str] = field(default_factory=dict)  # symbol -> reason, present but unusable
    universe_size_expected: Optional[int] = None
    universe_size_actual: int = 0
    coverage_issues: list = field(default_factory=list)


def _first(d: dict, keys) -> Optional[object]:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _to_epoch(value) -> Optional[int]:
    """Best-effort, non-fabricating timestamp normalization to epoch seconds UTC."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        # Heuristic: treat > 10^12 as milliseconds.
        if v > 1e12:
            v = v / 1000.0
        return int(v)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(float(s))
        except ValueError:
            pass
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.astimezone(timezone.utc).timestamp())
        except ValueError:
            return None
    return None


def _parse_candle(instrument: str, raw: dict) -> Optional[Candle]:
    """Map one raw candle dict to a Candle. `bid`/`ask`, if present, are
    simply never read — Candle has no field for them and the OHLCV-only
    strategy does not need them (a null bid/ask must never cause rejection
    here; only missing/invalid OHLCV does)."""
    if not isinstance(raw, dict):
        return None
    ts = _to_epoch(_first(raw, TIME_KEYS))
    o = _first(raw, OPEN_KEYS)
    h = _first(raw, HIGH_KEYS)
    l = _first(raw, LOW_KEYS)
    c = _first(raw, CLOSE_KEYS)
    v = _first(raw, VOLUME_KEYS)
    if ts is None or o is None or h is None or l is None or c is None:
        return None
    try:
        candle = Candle(
            instrument=instrument,
            timeframe="M1",
            ts=int(ts),
            open=float(o),
            high=float(h),
            low=float(l),
            close=float(c),
            volume=float(v) if v is not None else None,
            synthetic_from_m1=False,
        )
    except (TypeError, ValueError):
        return None
    return candle


def parse_payload(
    raw_text: str,
    fetched_at: Optional[int] = None,
    expected_universe_size: Optional[int] = None,
) -> FetchResult:
    """Parse a RealMarketAPI response against the confirmed live schema.

    Raises :class:`ApiError` if the JSON is invalid, ``symbols`` is absent
    or malformed, or the payload fails a FATAL provider-contract check
    (``timeframe``, ``candle_source``, ``synthetic_candles``, top-level
    ``status``) — these mean the payload cannot be trusted as genuine,
    provider-native, non-synthetic M1 data at all, so nothing in it is
    used. A ``universe_size`` shortfall is NOT fatal: whatever symbols
    *are* present and valid are still parsed and returned, with the gap
    reported via ``coverage_issues``/``rejected_symbols`` rather than
    silently treated as full, healthy coverage.
    """
    fetched_at = fetched_at if fetched_at is not None else int(time.time())
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ApiError(f"Invalid JSON from RealMarketAPI: {exc}") from exc

    if not isinstance(payload, dict):
        raise ApiError(f"Unexpected top-level JSON type from RealMarketAPI: {type(payload)}")

    symbols_block = payload.get("symbols")
    if not isinstance(symbols_block, dict):
        raise ApiError(
            "RealMarketAPI payload is missing a valid 'symbols' object — "
            "payload['symbols'] is the authoritative instrument universe "
            "and nothing can be parsed without it."
        )

    timeframe = payload.get("timeframe")
    candle_source = payload.get("candle_source")
    synthetic_candles = payload.get("synthetic_candles")
    status = payload.get("status")

    fatal = []
    if timeframe != REQUIRED_TIMEFRAME:
        fatal.append(f"timeframe={timeframe!r} (require {REQUIRED_TIMEFRAME!r})")
    if candle_source != REQUIRED_CANDLE_SOURCE:
        fatal.append(f"candle_source={candle_source!r} (require {REQUIRED_CANDLE_SOURCE!r})")
    if synthetic_candles is not False:
        fatal.append(f"synthetic_candles={synthetic_candles!r} (require False)")
    if status != REQUIRED_STATUS:
        fatal.append(f"status={status!r} (require {REQUIRED_STATUS!r})")
    if fatal:
        raise ApiError(
            "RealMarketAPI payload failed provider-contract validation — refusing to "
            "trust it rather than silently using possibly-synthetic or stale data: "
            + "; ".join(fatal)
        )

    provider_meta = ProviderMeta(
        schema_version=payload.get("schema_version"),
        service=payload.get("service"),
        provider=payload.get("provider"),
        timeframe=timeframe,
        candle_source=candle_source,
        synthetic_candles=synthetic_candles,
        generated_at=payload.get("generated_at"),
        status=status,
        universe_size=payload.get("universe_size"),
    )
    server_time = _to_epoch(provider_meta.generated_at)

    instruments: Dict[str, List[Candle]] = {}
    skipped: Dict[str, int] = {}
    symbol_meta: Dict[str, SymbolMeta] = {}
    rejected_symbols: Dict[str, str] = {}

    for raw_symbol, entry in symbols_block.items():
        symbol = str(raw_symbol)

        if not isinstance(entry, dict):
            rejected_symbols[symbol] = "symbol entry is not an object"
            continue

        symbol_meta[symbol] = SymbolMeta(
            symbol=str(entry.get("symbol", symbol)),
            market_state=entry.get("market_state"),
            status=entry.get("status"),
            last_candle_timestamp=entry.get("last_candle_timestamp"),
            candle_count=entry.get("candle_count"),
            gap_recoveries=entry.get("gap_recoveries"),
            rejected_count=entry.get("rejected_count"),
        )

        # The live provider's authoritative M1 array is "candles_1m" —
        # CONFIRMED from the live endpoint. Neither "candles" nor
        # "candles_l1" (an earlier, incorrect guess) is required or read;
        # a symbol simply has no usable data if "candles_1m" is
        # absent/invalid, full stop.
        raw_candles = entry.get("candles_1m")
        if not isinstance(raw_candles, list):
            rejected_symbols[symbol] = "missing or invalid 'candles_1m' array"
            continue

        candles: List[Candle] = []
        bad = 0
        for raw_c in raw_candles:
            candle = _parse_candle(symbol, raw_c)
            if candle is None:
                bad += 1
                continue
            candles.append(candle)
        candles.sort(key=lambda c: c.ts)
        skipped[symbol] = bad

        if not candles:
            rejected_symbols[symbol] = (
                "candles_1m array is empty"
                if not raw_candles
                else f"no valid candles parsed ({bad} of {len(raw_candles)} rejected)"
            )
            continue

        instruments[symbol] = candles

    universe_size_actual = len(symbols_block)
    coverage_issues: list = []
    if provider_meta.universe_size is not None and provider_meta.universe_size != universe_size_actual:
        coverage_issues.append(
            f"provider declared universe_size={provider_meta.universe_size} but the payload "
            f"contains {universe_size_actual} symbol entries"
        )
    if expected_universe_size is not None and universe_size_actual < expected_universe_size:
        # We only know the NAMES of symbols the provider actually mentioned
        # (rejected_symbols, below); any symbol never mentioned at all
        # cannot be named from this payload alone.
        coverage_issues.append(
            f"only {universe_size_actual}/{expected_universe_size} expected symbols are "
            "present in the payload at all"
        )
    if rejected_symbols:
        coverage_issues.append(
            f"{len(rejected_symbols)} symbol(s) present in the payload but unusable: "
            + ", ".join(f"{sym} ({reason})" for sym, reason in sorted(rejected_symbols.items()))
        )

    parse_errors = list(coverage_issues)
    if not instruments:
        parse_errors.append("No instruments could be parsed from the payload.")

    return FetchResult(
        ok=len(instruments) > 0,
        fetched_at=fetched_at,
        raw_bytes=len(raw_text.encode("utf-8", errors="ignore")),
        instruments=instruments,
        server_time=server_time,
        parse_errors=parse_errors,
        skipped_candles=skipped,
        provider_meta=provider_meta,
        symbol_meta=symbol_meta,
        rejected_symbols=rejected_symbols,
        universe_size_expected=expected_universe_size,
        universe_size_actual=universe_size_actual,
        coverage_issues=coverage_issues,
    )


class RealMarketApiClient:
    """Thin HTTP wrapper. ``fetch_fn`` is injectable for tests/offline demo."""

    def __init__(self, url: str, timeout: float = 15.0, fetch_fn=None):
        self.url = url
        self.timeout = timeout
        self._fetch_fn = fetch_fn

    def _default_fetch(self) -> str:
        if requests is None:
            raise ApiError("The 'requests' package is not installed.")
        resp = requests.get(self.url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.text

    def fetch(self, expected_universe_size: Optional[int] = None) -> FetchResult:
        try:
            if self._fetch_fn is not None:
                raw_text = self._fetch_fn()
            else:
                raw_text = self._default_fetch()
        except ApiError:
            raise
        except Exception as exc:  # network errors of any kind
            raise ApiError(f"RealMarketAPI request failed: {exc}") from exc
        return parse_payload(raw_text, expected_universe_size=expected_universe_size)
