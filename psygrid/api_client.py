"""RealMarketAPI client.

Fetches ``m1-live.json`` and parses it into :class:`psygrid.models.Candle`
objects, defensively. The exact schema of the live endpoint was not
reachable from the development sandbox this engine was built in, so the
parser accepts several plausible shapes/key-aliases rather than assuming one
exact layout. If the live payload uses field names outside the aliases
below, extend ``TIME_KEYS`` / ``OPEN_KEYS`` / etc. — nothing else in the
engine needs to change.

NEVER fabricates data: any candle that fails validation is dropped and
recorded as a data-quality issue, never guessed or interpolated.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

from .models import Candle

TIME_KEYS = ("time", "timestamp", "t", "ts", "datetime", "date")
OPEN_KEYS = ("open", "o")
HIGH_KEYS = ("high", "h")
LOW_KEYS = ("low", "l")
CLOSE_KEYS = ("close", "c")
VOLUME_KEYS = ("volume", "vol", "v", "tick_volume")
CANDLES_KEYS = ("candles", "bars", "m1", "data", "ohlc")
SYMBOL_KEYS = ("symbol", "instrument", "ticker", "pair")


class ApiError(Exception):
    """Raised for any problem talking to / parsing RealMarketAPI."""


@dataclass
class FetchResult:
    ok: bool
    fetched_at: int
    raw_bytes: int
    instruments: dict  # instrument -> list[Candle], sorted ascending by ts
    server_time: Optional[int]
    parse_errors: list
    skipped_candles: dict  # instrument -> count of dropped/invalid candles


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


def _extract_candle_list(entry) -> list:
    """Given a per-instrument entry, return the raw list of candle dicts."""
    if isinstance(entry, list):
        return entry
    if isinstance(entry, dict):
        found = _first(entry, CANDLES_KEYS)
        if isinstance(found, list):
            return found
    return []


def parse_payload(raw_text: str, fetched_at: Optional[int] = None) -> FetchResult:
    fetched_at = fetched_at if fetched_at is not None else int(time.time())
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ApiError(f"Invalid JSON from RealMarketAPI: {exc}") from exc

    parse_errors: list = []
    instruments: dict = {}
    skipped: dict = {}
    server_time = None

    if isinstance(payload, dict):
        server_time = _to_epoch(payload.get("server_time") or payload.get("timestamp"))
        # Shape A: {"instruments": {SYM: {...}}}
        instr_block = payload.get("instruments")
        if isinstance(instr_block, dict):
            entries = instr_block.items()
        else:
            # Shape B: top-level dict keyed directly by instrument symbol.
            reserved = {"instruments", "server_time", "timestamp", "status", "meta"}
            entries = [(k, v) for k, v in payload.items() if k not in reserved]
        for symbol, entry in entries:
            raw_list = _extract_candle_list(entry)
            if not raw_list and isinstance(entry, dict):
                continue
            candles = []
            bad = 0
            for raw_c in raw_list:
                candle = _parse_candle(str(symbol), raw_c)
                if candle is None:
                    bad += 1
                    continue
                candles.append(candle)
            candles.sort(key=lambda c: c.ts)
            instruments[str(symbol)] = candles
            skipped[str(symbol)] = bad
    elif isinstance(payload, list):
        # Shape C: [{"symbol": "XAUUSD", "candles": [...]}, ...]
        for item in payload:
            if not isinstance(item, dict):
                continue
            symbol = _first(item, SYMBOL_KEYS)
            if symbol is None:
                continue
            raw_list = _extract_candle_list(item)
            candles = []
            bad = 0
            for raw_c in raw_list:
                candle = _parse_candle(str(symbol), raw_c)
                if candle is None:
                    bad += 1
                    continue
                candles.append(candle)
            candles.sort(key=lambda c: c.ts)
            instruments[str(symbol)] = candles
            skipped[str(symbol)] = bad
    else:
        raise ApiError(f"Unexpected top-level JSON type from RealMarketAPI: {type(payload)}")

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

    def fetch(self) -> FetchResult:
        try:
            if self._fetch_fn is not None:
                raw_text = self._fetch_fn()
            else:
                raw_text = self._default_fetch()
        except ApiError:
            raise
        except Exception as exc:  # network errors of any kind
            raise ApiError(f"RealMarketAPI request failed: {exc}") from exc
        return parse_payload(raw_text)
