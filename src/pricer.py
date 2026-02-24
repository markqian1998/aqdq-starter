# src/pricer.py
"""Top-level pricing API for AQ/DQ structured products.

Typical usage
-------------
    from src.pricer import price_with_greeks

    result = price_with_greeks(
        terms=terms, schedule=sched,
        today=date(2025, 8, 20),
        spot=51.70, r=0.03, q=0.01, vol=0.474,
        n_paths=20000, settlement_mode="daily",
    )
    print(result["pv"], result["greeks"])
"""

from __future__ import annotations
from datetime import date
from typing import Dict, Any, Optional

from src.market.snapshot import EquitySnapshot, build_snapshot_auto
from src.engines.mc import price_aqdq_mc, MCSettings
from src.engines.payoff import AQDQRuntimeState


def price_with_greeks(
    terms,
    schedule,
    today: date,
    spot: Optional[float],
    r: Optional[float],
    q: Optional[float],
    vol: float,
    delivered_to_date: float = 0.0,
    n_paths: int = 20000,
    settlement_mode: str = "final",
    use_auto_curve: bool = True,         # auto-select SOFR (US) or HIBOR (HK) discount curve
) -> Dict[str, Any]:
    """Price an AQ/DQ product and compute Delta, Gamma, Vega.

    Parameters
    ----------
    terms            : AQDQTerms instance (from src.models.terms)
    schedule         : AQDQSchedule instance
    today            : valuation date
    spot             : current stock price (overrides live fetch when use_auto_curve=True)
    r                : risk-free rate (used only when use_auto_curve=False)
    q                : dividend yield (overrides live fetch when use_auto_curve=True)
    vol              : implied volatility (annualised, e.g. 0.45 = 45%)
    delivered_to_date: shares already settled before today (deducted from remaining cap)
    n_paths          : Monte Carlo path count (higher = lower noise, slower)
    settlement_mode  : 'final' (single settlement at maturity) or 'daily' (mark-to-market)
    use_auto_curve   : True -> auto-fetch live curve (SOFR/HIBOR); False -> flat r/q fallback

    Returns
    -------
    dict with keys: pv, std_err, greeks (delta, gamma, vega), meta
    """
    if use_auto_curve:
        # Auto-detect market (US/HK) and load the appropriate discount curve
        mkt = build_snapshot_auto(
            ticker=terms.ticker or "",
            today=today,
            vol=vol,
            exchange_hint=getattr(terms, "exchange_hint", None),
            spot_override=spot,         # use manually supplied spot if provided
            override_div_yield=q,       # use manually supplied div yield if provided
        )
    else:
        # Legacy flat-rate path (for scripts that do not need live curves)
        mkt = EquitySnapshot.flat(
            today=today, spot=spot if spot is not None else 0.0,
            vol=vol, r=float(r or 0.0), q=float(q or 0.0),
            market="US", curve_ccy="USD"
        )

    rt  = AQDQRuntimeState(today=today, delivered_to_date=delivered_to_date)
    cfg = MCSettings(n_paths=n_paths)

    return price_aqdq_mc(
        terms=terms, schedule=schedule, mkt=mkt, rt=rt, settings=cfg,
        enable_pnbd=True, settlement_mode=settlement_mode
    )
