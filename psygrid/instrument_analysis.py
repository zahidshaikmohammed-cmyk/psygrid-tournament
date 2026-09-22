"""Per-instrument analysis orchestrator.

Combines data quality, derived timeframes, structure, momentum, volatility,
liquidity, price behaviour, setup detection, execution quality and time
behaviour into one :class:`~psygrid.models.InstrumentAnalysis`. The exact
same call sequence runs for every instrument — this module is the one place
that defines "the common analytical framework" the spec requires.
"""

from __future__ import annotations

from typing import List, Optional

from .candle_store import IngestReport
from .config import Config
from .data_quality import assess as assess_data_quality
from .execution import analyze_execution
from .liquidity import analyze_liquidity
from .models import Candle, InstrumentAnalysis
from .momentum import analyze_momentum
from .price_behavior import analyze_price_behavior
from .setups import detect_best_setup
from .structure import analyze_structure
from .time_behavior import analyze_time_behavior
from .timeframes import aggregate_all
from .volatility import analyze_volatility

CONTEXT_TIMEFRAME = "M15"


def analyze_instrument(
    instrument: str,
    m1_candles: List[Candle],
    ingest_report: IngestReport,
    now_ts: int,
    config: Config,
) -> InstrumentAnalysis:
    data_quality = assess_data_quality(
        instrument,
        m1_candles,
        ingest_report,
        now_ts,
        config.freshness_max_seconds,
        config.history_min_candles,
    )

    current_price = m1_candles[-1].close if m1_candles else None

    analysis = InstrumentAnalysis(
        instrument=instrument,
        as_of=now_ts,
        current_price=current_price,
        data_quality=data_quality,
    )

    if not data_quality.is_usable:
        # Data is unreliable: report quality only, everything else stays
        # UNKNOWN rather than being computed on untrustworthy input.
        return analysis

    derived = aggregate_all(m1_candles)
    structure_m1 = analyze_structure(m1_candles, "M1")
    analysis.structure["M1"] = structure_m1
    for tf, candles in derived.items():
        analysis.structure[tf] = analyze_structure(candles, tf) if candles else None

    structure_context = analysis.structure.get(CONTEXT_TIMEFRAME) or structure_m1

    momentum = analyze_momentum(m1_candles)
    analysis.momentum = momentum

    volatility = analyze_volatility(m1_candles)
    analysis.volatility = volatility

    h1_candles = derived.get("H1", [])
    liquidity = analyze_liquidity(m1_candles, h1_candles, volatility.atr)
    analysis.liquidity = liquidity

    price_behavior = analyze_price_behavior(m1_candles, structure_m1, volatility)
    analysis.price_behavior = price_behavior

    setup = detect_best_setup(
        instrument,
        m1_candles,
        structure_m1,
        structure_context,
        momentum,
        volatility,
        liquidity,
        price_behavior,
    )
    analysis.setup = setup

    if setup is not None:
        execution = analyze_execution(setup, volatility.atr)
        analysis.execution = execution
        risk = execution.risk
        time_behavior = analyze_time_behavior(
            now_ts,
            setup.formation_time,
            current_price,
            setup.entry_candidate,
            risk,
            config.max_setup_age_minutes,
            config.max_travel_ratio,
        )
        analysis.time_behavior = time_behavior

    analysis.regime = _classify_regime(structure_context, volatility)
    return analysis


def _classify_regime(structure_context, volatility) -> str:
    trend_part = structure_context.trend.value if structure_context else "UNKNOWN"
    if volatility.abnormal:
        vol_part = "ABNORMAL_VOL"
    elif volatility.expansion:
        vol_part = "EXPANSION"
    elif volatility.compression:
        vol_part = "COMPRESSION"
    else:
        vol_part = "NORMAL_VOL"
    return f"{trend_part}_{vol_part}"
