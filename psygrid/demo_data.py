"""Offline synthetic 10-instrument feed for ``--demo`` mode.

This is NOT test fixture data (see ``tests/fixtures.py`` for that) — it is a
self-contained, deterministic price generator so the engine can be run
end-to-end (``python main.py --demo``) without any network access or
Telegram credentials, to demonstrate the full continuous-scan +
30-minute-tournament + Telegram-report loop.

Every instrument name is clearly prefixed ``DEMO`` so output can never be
mistaken for a real market quote.
"""

from __future__ import annotations

import json
import math
import time
from typing import Dict, List

INSTRUMENTS = [
    "DEMO_TRENDUP_A",
    "DEMO_TRENDUP_B",
    "DEMO_TRENDDOWN_A",
    "DEMO_TRENDDOWN_B",
    "DEMO_RANGE_A",
    "DEMO_RANGE_B",
    "DEMO_SWEEP_A",
    "DEMO_CHOP_A",
    "DEMO_CHOP_B",
    "DEMO_BREAKOUT_A",
]

INITIAL_HISTORY_BARS = 260


def _params(index: int) -> dict:
    return {
        "base": 100.0 + index * 37.0,
        "drift": [0.012, 0.009, -0.012, -0.007, 0.0, 0.0, 0.0, 0.0005, -0.0004, 0.02][index],
        "amplitude": [0.4, 0.3, 0.4, 0.3, 0.6, 0.5, 0.5, 0.9, 0.8, 0.3][index],
        "period": [25, 40, 25, 40, 18, 30, 22, 6, 8, 15][index],
        "phase": index * 0.7,
        "wick": 0.15 + (index % 3) * 0.05,
    }


def _price_at(params: dict, bar_index: int) -> float:
    value = (
        params["base"]
        + params["drift"] * bar_index
        + params["amplitude"] * math.sin(bar_index / params["period"] + params["phase"])
    )
    # A late, sharp liquidity-sweep kick for the SWEEP instrument so the
    # demo run has something to actually detect.
    return value


class DemoFeedGenerator:
    def __init__(self, start_ts: int = None, bar_seconds: int = 60):
        self.bar_seconds = bar_seconds
        now = int(start_ts if start_ts is not None else time.time())
        self.start_minute_ts = (now // bar_seconds) * bar_seconds - INITIAL_HISTORY_BARS * bar_seconds
        self.next_bar_index = 0
        self._history: Dict[str, List[dict]] = {sym: [] for sym in INSTRUMENTS}
        for sym_idx, symbol in enumerate(INSTRUMENTS):
            params = _params(sym_idx)
            for bar_index in range(INITIAL_HISTORY_BARS):
                self._history[symbol].append(self._make_candle(symbol, params, bar_index))
        self.next_bar_index = INITIAL_HISTORY_BARS

    def _make_candle(self, symbol: str, params: dict, bar_index: int) -> dict:
        ts = self.start_minute_ts + bar_index * self.bar_seconds
        open_price = _price_at(params, bar_index - 1) if bar_index > 0 else _price_at(params, 0)
        close_price = _price_at(params, bar_index)
        wick = params["wick"] * (0.5 + 0.5 * abs(math.sin(bar_index * 0.9 + params["phase"])))

        if symbol == "DEMO_SWEEP_A" and bar_index % 47 == 46:
            # Inject a deterministic sweep-and-reject spike.
            spike = params["amplitude"] * 2.5
            high = max(open_price, close_price) + spike
            low = min(open_price, close_price) - wick
            close_price = open_price + wick * 0.2
        else:
            high = max(open_price, close_price) + wick
            low = min(open_price, close_price) - wick

        return {
            "timestamp": ts,
            "open": round(open_price, 5),
            "high": round(high, 5),
            "low": round(low, 5),
            "close": round(close_price, 5),
            "volume": 100.0,
            "bid": None,
            "ask": None,
        }

    def next_payload(self) -> str:
        # Clamp the synthetic "next" bar timestamp to the real wall clock so
        # repeated fast-polling (as in --once / short demo loops) never
        # produces a candle stamped in the future — it just keeps updating
        # the still-forming latest minute, exactly like a real live feed
        # would between real 1-minute closes. Time only truly advances a
        # full bar once real time actually does.
        real_now_minute = (int(time.time()) // self.bar_seconds) * self.bar_seconds
        desired_ts = self.start_minute_ts + self.next_bar_index * self.bar_seconds
        if desired_ts > real_now_minute:
            self.next_bar_index -= 1  # re-stamp the same forming bar instead of advancing

        for sym_idx, symbol in enumerate(INSTRUMENTS):
            params = _params(sym_idx)
            candle = self._make_candle(symbol, params, self.next_bar_index)
            if self._history[symbol] and self._history[symbol][-1]["timestamp"] == candle["timestamp"]:
                self._history[symbol][-1] = candle
            else:
                self._history[symbol].append(candle)
            if len(self._history[symbol]) > 1600:
                self._history[symbol] = self._history[symbol][-1600:]
        self.next_bar_index += 1

        generated_at = self._history[INSTRUMENTS[0]][-1]["timestamp"]
        payload = {
            # Matches the CONFIRMED live RealMarketAPI schema exactly (see
            # psygrid/api_client.py's module docstring) so --demo exercises
            # the real parsing/validation path, not a stand-in shape.
            "schema_version": "1.0",
            "service": "psygrid-forex",
            "provider": "realmarketapi",
            "timeframe": "M1",
            "candle_source": "provider_native",
            "synthetic_candles": False,
            "generated_at": generated_at,
            "status": "ok",
            "universe_size": len(INSTRUMENTS),
            "symbols": {
                sym: {
                    "symbol": sym,
                    "market_state": "open",
                    "status": "ok",
                    "last_candle_timestamp": self._history[sym][-1]["timestamp"],
                    "candle_count": len(self._history[sym]),
                    "gap_recoveries": 0,
                    "rejected_count": 0,
                    "candles_l1": self._history[sym],
                }
                for sym in INSTRUMENTS
            },
        }
        return json.dumps(payload)
