# src/analytics/multi_scenario.py
"""Run several drift scenarios in one shot.

Why this exists
---------------
The AI draft of the NVDA accumulator note collapsed two distinct measures
into one number:
    - the risk-neutral measure (drift = r - q - b) — used for valuation,
      MTM, and Greeks
    - a real-world measure (e.g. drift = +60% historical / -20% bearish)
      — used to *describe* what KO probability and accumulation look like
      under a given macro view.

Both are legitimate. Mixing them is not.

This module enforces the separation by running both in parallel and
returning a dict keyed by scenario name. Reports then explicitly label
"risk-neutral" rows as the only ones suitable for fair value, with the
bull/bear rows shown alongside for buy-side risk illustration.

DEALER NOTE: This is exactly how dealer trade approvals work:
1. Compute fair PV and Greeks under risk-neutral.
2. Stress: replay the trade under +1sd / -1sd realised drift and several
   historical analogs (e.g. "2008Q4-like", "2020Q1-like").
3. Show both side-by-side.
"""

from __future__ import annotations
from typing import Dict, List

from src.market.snapshot import EquitySnapshot
from src.engines.mc import MCSettings
from src.engines.payoff import AQDQRuntimeState

from src.io.trade_config import ScenarioSpec
from src.analytics.scenarios import ScenarioResult, run_scenario_analysis


def run_all_scenarios(
    *,
    terms,
    schedule,
    mkt: EquitySnapshot,
    rt: AQDQRuntimeState,
    settings: MCSettings,
    scenarios: List[ScenarioSpec],
    settlement_mode: str = "daily",
) -> Dict[str, ScenarioResult]:
    """Run every scenario in `scenarios` and return a {name: ScenarioResult} dict.

    Greeks are only computed for the risk-neutral scenario (drift_pct=None).
    For bull/bear scenarios Greeks have no canonical meaning, so we skip
    them — saving ~3x compute (Greeks require 4 extra reprices).
    """
    results: Dict[str, ScenarioResult] = {}

    for spec in scenarios:
        is_risk_neutral = spec.drift_pct is None
        if is_risk_neutral:
            mkt_scenario = mkt
        else:
            mkt_scenario = mkt.with_drift_override(spec.drift_pct)

        result = run_scenario_analysis(
            terms=terms,
            schedule=schedule,
            mkt=mkt_scenario,
            rt=rt,
            settings=settings,
            settlement_mode=settlement_mode,
            scenario_name=spec.name,
            compute_greeks=is_risk_neutral,
        )
        results[spec.name] = result

    return results
