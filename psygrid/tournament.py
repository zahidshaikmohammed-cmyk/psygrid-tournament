"""The 30-minute tournament: every instrument competes under identical rules.

Produces exactly one of:
    - a single winning TournamentRow (QUALIFIED, score >= min_quality_score)
    - no winner at all (NO TRADE) — never forced.
"""

from __future__ import annotations

from typing import Dict, Optional

from .config import Config
from .models import CandidateStatus, InstrumentAnalysis, TournamentResult, TournamentRow
from .scoring import HistoricalStatsFn, evaluate_hard_gates, score_candidate


def run_tournament(
    analyses: Dict[str, InstrumentAnalysis],
    config: Config,
    ts: int,
    historical_stats_fn: Optional[HistoricalStatsFn] = None,
) -> TournamentResult:
    rows = []
    usable_count = 0

    for instrument, analysis in analyses.items():
        if analysis.data_quality.history_count > 0:
            usable_count += 1
        freshness = analysis.data_quality.freshness_seconds
        setup_age = analysis.time_behavior.setup_age_minutes if analysis.time_behavior else None

        gate = evaluate_hard_gates(analysis, config)
        if not gate.passed:
            rows.append(
                TournamentRow(
                    instrument=instrument,
                    status=CandidateStatus.REJECT,
                    score=None,
                    breakdown=None,
                    reasons=gate.reasons,
                    setup=analysis.setup,
                    execution=analysis.execution,
                    data_freshness_seconds=freshness,
                    setup_age_minutes=setup_age,
                )
            )
            continue

        breakdown = score_candidate(analysis, config, historical_stats_fn)
        rows.append(
            TournamentRow(
                instrument=instrument,
                status=CandidateStatus.QUALIFIED,
                score=breakdown.final_score,
                breakdown=breakdown,
                reasons=[],
                setup=analysis.setup,
                execution=analysis.execution,
                data_freshness_seconds=freshness,
                setup_age_minutes=setup_age,
            )
        )

    def _tie_break_key(row: TournamentRow):
        # Deterministic ranking even on an exact score tie: prefer higher
        # R:R, then alphabetical instrument name as the final tiebreaker so
        # results are reproducible rather than dependent on dict ordering.
        rr = row.execution.rr if row.execution else 0.0
        return (-row.score, -rr, row.instrument)

    qualified = sorted(
        [r for r in rows if r.status == CandidateStatus.QUALIFIED],
        key=_tie_break_key,
    )
    rejected = [r for r in rows if r.status == CandidateStatus.REJECT]

    winner = None
    second = None
    lead = None
    if qualified and qualified[0].score >= config.min_quality_score:
        winner = qualified[0]
        if len(qualified) > 1:
            second = qualified[1]
            lead = round(winner.score - second.score, 2)

    coverage = f"{usable_count}/{max(config.expected_instrument_count, len(analyses))}"

    ordered_rows = qualified + rejected

    return TournamentResult(
        ts=ts,
        rows=ordered_rows,
        winner=winner,
        second=second,
        lead=lead,
        candidates_evaluated=len(analyses),
        data_coverage=coverage,
        min_quality_threshold=config.min_quality_score,
    )
