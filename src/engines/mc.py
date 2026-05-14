# src/engines/mc.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any
import numpy as np
from datetime import date
import QuantLib as ql

from src.market.snapshot import EquitySnapshot
from src.engines.paths import gbm_paths_antithetic, gbm_paths_local_vol_antithetic
from src.engines.payoff import pathwise_shares_and_ko, AQDQRuntimeState


@dataclass
class MCSettings:
    n_paths: int = 500000   # number of Monte Carlo paths (more paths = lower noise, higher cost)
    seed: int = 20250301    # random seed — fixing it ensures common random numbers for stable Greeks
    spot_bump: float = 0.01  # relative spot bump ±1% for Delta / Gamma
    vol_bump: float = 0.01   # absolute vol bump ±1 vol point for Vega


def yearfractions(dc: ql.DayCounter, today: date, obs_dates: list[date]) -> np.ndarray:
    """Convert a list of observation dates into year-fractions relative to today.

    Uses the provided QuantLib DayCounter (e.g. Actual365Fixed) to compute
    t_1, t_2, ..., t_N — the time axis required by the GBM simulation.
    Falls back to Actual365Fixed if dc is None.
    """
    if dc is None:
        dc = ql.Actual365Fixed()
    ql_today = ql.Date(today.day, today.month, today.year)
    return np.array([dc.yearFraction(ql_today, ql.Date(d.day, d.month, d.year)) for d in obs_dates], dtype=float)


def _simulate_spots(
    *,
    mkt: EquitySnapshot,
    times: np.ndarray,
    T: float,
    settings: MCSettings,
    spot: float | None = None,
    vol: float | None = None,
    parallel_vol_bump: float = 0.0,
) -> np.ndarray:
    S0 = mkt.spot if spot is None else float(spot)
    drift = mkt.fwd_drift(t=T)
    if getattr(mkt, "vol_surface", None) is not None:
        return gbm_paths_local_vol_antithetic(
            S0=S0,
            times=times,
            r_minus_q=drift,
            vol_fn=lambda t, s: mkt.path_vol(t, s, parallel_bump=parallel_vol_bump),
            n_paths=settings.n_paths,
            seed=settings.seed,
        )
    sigma = mkt.vol if vol is None else float(vol)
    return gbm_paths_antithetic(
        S0=S0, times=times, r_minus_q=drift,
        sigma=sigma, n_paths=settings.n_paths, seed=settings.seed,
    )


def price_aqdq_mc(
    *,
    terms,
    schedule,
    mkt: EquitySnapshot,
    rt: AQDQRuntimeState,
    settings: MCSettings,
    enable_pnbd: bool = True,
    settlement_mode: str = "final",  # "final" (lump at maturity) or "daily" (mark-to-market)
) -> Dict[str, Any]:
    """Price an AQ/DQ product via Monte Carlo simulation.

    Returns a dict with keys: pv, std_err, greeks (delta, gamma, vega), meta.

    mkt is today's market snapshot (EquitySnapshot): spot, rate curve, div_yield, vol.
    fwd_drift() = r - q is the risk-neutral drift used by the GBM simulation.
    Antithetic variates (Z and -Z) are used to reduce variance.
    S_T is the terminal price of each simulated path.
    """
    # 1) Remaining observation dates (only future dates enter the valuation)
    # Past settlements are realised P&L and do not contribute to today's mark-to-market.
    obs_rem = schedule.remaining_observation_dates(terms, rt.today, include_today_close=False)
    if not obs_rem:
        return {"pv": 0.0, "std_err": 0.0, "greeks": {"delta": 0.0, "gamma": 0.0, "vega": 0.0}}
    times = yearfractions(schedule.dc, rt.today, obs_rem)
    T = times[-1]  # year-fraction to the final observation date; used for discounting

    # GTD days remaining as of the valuation date
    gtd_rem = schedule.gtd_days_remaining(terms, rt.today, include_today_close=False)
    effective_enable_pnbd = bool(enable_pnbd and getattr(terms, "enable_pnbd", True))

    # Speedy mode: determine the index in the remaining schedule for the lump-sum GTD grant
    # Rule: if at least two observation dates remain and GTD is not yet exhausted, use index 1 (day 2).
    lump_idx = None
    if terms.aq_mode == "speedy":
        if len(obs_rem) >= 2 and gtd_rem > 0:
            lump_idx = 1

    # 2) Generate price paths (common random numbers ensure stable finite-difference Greeks)
    # DEALER NOTE: fwd_drift(t=T) uses the zero rate at maturity T from the discount curve,
    # minus dividend yield q, minus borrow spread b. This is the risk-neutral drift used by
    # every tier-1 dealer for European-style barrier products. For long-dated trades with
    # significant curve slope, a more refined approach uses per-step short rates — left as a
    # future upgrade (LSV / hybrid IR pending).
    spots = _simulate_spots(mkt=mkt, times=times, T=T, settings=settings)
    S_T = spots[:, -1]

    # 4) Compute per-path shares and KO indices
    total_shares, ko_idx, daily_shares = pathwise_shares_and_ko(
        spots=spots, S_T=S_T,
        forward_price=terms.forward_price,
        ko_level=terms.ko_level, ko_dir=terms.ko_direction,
        shares_per_day=terms.shares_per_day, gear_ratio=terms.gear_ratio,
        gtd_days_remaining=gtd_rem,
        max_total_shares=terms.max_total_shares,
        delivered_to_date=rt.delivered_to_date,
        side=terms.side, lnbd_dir=terms.lnbd_direction,
        aq_mode=getattr(terms, "aq_mode", "regular"),
        gtd_lump_index_in_remaining=lump_idx,
        enable_pnbd=effective_enable_pnbd,
    )
    # total_shares: cumulative shares per path
    # ko_idx:       observation-day index of KO event (-1 if no KO)

    # 5) Discount cash flows according to settlement mode
    if settlement_mode == "daily":
        # Daily settlement: each day's shares are valued at that day's spot and discounted separately
        dfs = np.array([mkt.df(t) for t in times], dtype=float)           # (n_steps,)
        payoff_mat = (spots - terms.forward_price) * daily_shares          # (n_paths, n_steps)
        pv_paths = payoff_mat @ dfs
    elif settlement_mode == "final":
        # Final settlement: all shares are sold at the terminal price and discounted once
        pv_paths = mkt.df(T) * (S_T - terms.forward_price) * total_shares
    else:
        raise ValueError("settlement_mode must be 'final' or 'daily'")

    pv = float(np.mean(pv_paths))
    std_err = float(np.std(pv_paths, ddof=1) / np.sqrt(settings.n_paths))

    # 6) Greeks via finite-difference bump-and-reprice (common random numbers)
    bump_S = settings.spot_bump
    bump_v = settings.vol_bump

    def reprice(spot=None, vol=None, vol_bump: float = 0.0):
        S0  = mkt.spot if spot is None else spot
        sig = mkt.vol  if vol  is None else vol
        sp  = _simulate_spots(
            mkt=mkt, times=times, T=T, settings=settings,
            spot=S0, vol=sig, parallel_vol_bump=vol_bump,
        )
        ST  = sp[:, -1]
        sh, _, dsh = pathwise_shares_and_ko(
            spots=sp, S_T=ST,
            forward_price=terms.forward_price,
            ko_level=terms.ko_level, ko_dir=terms.ko_direction,
            shares_per_day=terms.shares_per_day, gear_ratio=terms.gear_ratio,
            gtd_days_remaining=gtd_rem, max_total_shares=terms.max_total_shares,
            delivered_to_date=rt.delivered_to_date, side=terms.side, lnbd_dir=terms.lnbd_direction,
            aq_mode=getattr(terms, "aq_mode", "regular"),
            gtd_lump_index_in_remaining=lump_idx,
            enable_pnbd=effective_enable_pnbd,
        )
        if settlement_mode == "daily":
            dfs = np.array([mkt.df(t) for t in times], dtype=float)
            return float(np.mean(((sp - terms.forward_price) * dsh) @ dfs))
        else:
            return float(np.mean(mkt.df(T) * (ST - terms.forward_price) * sh))

    pv_up, pv_dn = reprice(mkt.spot * (1 + bump_S)), reprice(mkt.spot * (1 - bump_S))
    delta = (pv_up - pv_dn) / (mkt.spot * (1 + bump_S) - mkt.spot * (1 - bump_S))
    gamma = (pv_up - 2 * pv + pv_dn) / ((0.5 * (mkt.spot * (1 + bump_S) - mkt.spot * (1 - bump_S))) ** 2)

    if getattr(mkt, "vol_surface", None) is not None:
        pv_vup, pv_vdn = reprice(vol_bump=bump_v), reprice(vol_bump=-bump_v)
    else:
        pv_vup, pv_vdn = reprice(vol=mkt.vol + bump_v), reprice(vol=max(1e-6, mkt.vol - bump_v))
    vega = (pv_vup - pv_vdn) / (2 * bump_v)

    ko_prob = float(np.mean(ko_idx >= 0))
    avg_shares = float(np.mean(total_shares))
    avg_daily_first10 = float(np.mean(daily_shares[:, :min(10, daily_shares.shape[1])]))

    return {
        "pv": pv,
        "std_err": std_err,
        "greeks": {"delta": float(delta), "gamma": float(gamma), "vega": float(vega)},
        "meta": {
            "n_paths": settings.n_paths,
            "T": T,
            "n_steps": len(times),
            "gtd_remaining": gtd_rem,
            "settlement_mode": settlement_mode,
            "ko_prob": ko_prob,
            "avg_total_shares": avg_shares,
            "avg_daily_shares_first10": avg_daily_first10
        }
    }
