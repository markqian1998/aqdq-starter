# src/analytics/scenarios.py
"""Enhanced Monte Carlo analytics for AQ/DQ accumulator products.

Building on the core `price_aqdq_mc` engine (which gives PV / std_err / Greeks),
this module computes the dealer-grade risk metrics that actually drive
accumulator decisions:

    - KO probability and timing distribution
    - Share accumulation distribution (median, p5, p95, expected, tail)
    - Tail-risk table: P(total_shares >= threshold) + corresponding cash outlay
    - Terminal MTM P&L distribution
    - A small sample of representative paths for plotting

WHY THESE METRICS, NOT JUST PV
------------------------------
PV / Greeks tell the dealer / hedger what to do TODAY. They do not tell a
buy-side PM what to worry about.

What kills accumulator clients is not the average outcome — it is the
specific path where:
    (a) the underlying drops below strike,
    (b) the 2x gear engages, doubling daily accumulation,
    (c) KO does not fire (because spot is below KO),
    (d) the client is forced to keep buying into weakness, for the FULL
        remaining tenor.

The "fat right tail" of shares accumulated is the risk-control variable.
Dealers report this as P(shares >= X% of max) for several X. Our
`tail_risk_table` mirrors that convention.

DEALER NOTE: Tier-1 desks aggregate these per-trade tail-risk vectors
across the entire accumulator book to compute "book-level cash-at-risk"
and "book-level forced delivery risk." That's Phase 4 territory; for
Phase 1 we keep it single-trade.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from datetime import date

import numpy as np
import QuantLib as ql

from src.market.snapshot import EquitySnapshot
from src.engines.mc import MCSettings, yearfractions
from src.engines.paths import gbm_paths_antithetic, gbm_paths_local_vol_antithetic
from src.engines.payoff import pathwise_shares_and_ko, AQDQRuntimeState


# --------------------------------------------------------------------- #
# Result dataclass
# --------------------------------------------------------------------- #

@dataclass
class TailRiskRow:
    """One row of the tail-risk table: 'P(shares >= threshold) and the
    corresponding strike-cash outlay if that threshold is reached'."""
    threshold_pct_of_cap: float    # 0.25, 0.50, 0.75, 1.00
    threshold_shares: float        # absolute shares at this percentile
    probability: float             # P(total_shares >= threshold_shares)
    cash_outlay_at_strike: float   # threshold_shares * forward_price (USD/HKD)


@dataclass
class ScenarioResult:
    """Complete output of one MC scenario run."""

    # --- identifying info ---
    scenario_name: str
    drift_used: float                          # the actual drift fed into GBM (annualised)

    # --- valuation / hedging (only meaningful under risk-neutral drift) ---
    pv: float
    std_err: float
    greeks: Dict[str, float]                   # delta, gamma, vega (absolute)

    # --- product-mechanic statistics (meaningful under any drift) ---
    ko_probability: float
    ko_timing_distribution: Dict[int, float]   # obs index -> probability mass
    expected_ko_period: Optional[float]        # E[ko_index | KO occurred]
    median_ko_period: Optional[int]

    # --- share-accumulation distribution ---
    expected_shares: float
    median_shares: float
    p5_shares: float
    p95_shares: float
    p99_shares: float
    max_shares: float                          # max observed in simulation (NOT cap)
    shares_histogram: Dict[str, float]         # binned distribution for plotting

    # --- tail-risk table (the buy-side conversation) ---
    tail_risk_table: List[TailRiskRow]

    # --- terminal MTM distribution ---
    expected_mtm: float
    median_mtm: float
    p5_mtm: float
    p95_mtm: float
    prob_terminal_loss: float

    # --- client-facing P&L return distribution ---
    # Defined pathwise as discounted P&L / discounted strike cash outlay.
    expected_pnl_return: float
    median_pnl_return: float
    p5_pnl_return: float
    p95_pnl_return: float
    pnl_return_buckets: Dict[str, float]

    # --- representative sample of paths for plotting ---
    sample_paths: np.ndarray = field(repr=False)   # shape (k, n_steps), k ~ 100
    sample_path_ko_idx: np.ndarray = field(repr=False)  # KO index for each sample path

    # --- time axis for plots ---
    obs_dates: List[date]
    times_yr: np.ndarray = field(repr=False)

    # --- meta ---
    n_paths: int
    settlement_mode: str
    underlying: str
    forward_price: float
    ko_level: float
    notional_cap_shares: Optional[float]

    # --- normalized Greeks (cash + % of notional @strike and @spot) ---
    # Placed last so the dataclass field ordering stays valid; defaults to {}
    # for bull/bear scenarios where Greeks are not computed.
    greeks_normalized: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serializable view (numpy arrays → lists, dates → ISO strings)."""
        d = asdict(self)
        d["sample_paths"] = self.sample_paths.tolist()
        d["sample_path_ko_idx"] = self.sample_path_ko_idx.tolist()
        d["times_yr"] = self.times_yr.tolist()
        d["obs_dates"] = [dt.isoformat() for dt in self.obs_dates]
        return d


# --------------------------------------------------------------------- #
# Core routine
# --------------------------------------------------------------------- #

# Default tail-risk thresholds: 25/50/75/100 % of max shares.
# (If cap=None we fall back to using p95 as the "ceiling".)
_DEFAULT_TAIL_PCTS = (0.25, 0.50, 0.75, 1.00)


def _ko_timing_distribution(ko_idx: np.ndarray, n_steps: int) -> Dict[int, float]:
    """P(KO at observation index i) for i in [0, n_steps), plus -1 for 'no KO'."""
    # ko_idx is -1 if no KO else the index. We compute frequencies.
    out: Dict[int, float] = {}
    n = len(ko_idx)
    # No-KO bucket
    out[-1] = float(np.mean(ko_idx < 0))
    # Per-period buckets (only include ones with non-zero mass to keep dict small)
    vals, counts = np.unique(ko_idx[ko_idx >= 0], return_counts=True)
    for v, c in zip(vals, counts):
        out[int(v)] = float(c / n)
    return out


def _histogram(values: np.ndarray, bins: int = 30) -> Dict[str, float]:
    """Histogram as {'edge_lo|edge_hi': probability_mass}.

    Stored as strings so the dict is JSON-friendly.
    """
    h, edges = np.histogram(values, bins=bins, density=False)
    total = float(h.sum())
    if total == 0:
        return {}
    return {
        f"{edges[i]:.2f}|{edges[i+1]:.2f}": float(h[i] / total)
        for i in range(len(h))
    }


def _pnl_return_buckets(values: np.ndarray) -> Dict[str, float]:
    """Client-facing return buckets for P&L / strike cash outlay.

    Values are decimals, so 1.00 means +100% of deployed strike cash.
    The high-return buckets are intentionally explicit because low-strike
    accumulators often create investor questions around PnL 80-100% and
    PnL >100% scenarios.
    """
    buckets = [
        ("<= -50%", None, -0.50),
        ("-50% to -25%", -0.50, -0.25),
        ("-25% to 0%", -0.25, 0.00),
        ("0% to 20%", 0.00, 0.20),
        ("20% to 40%", 0.20, 0.40),
        ("40% to 60%", 0.40, 0.60),
        ("60% to 80%", 0.60, 0.80),
        ("80% to 100%", 0.80, 1.00),
        ("> 100%", 1.00, None),
    ]
    out: Dict[str, float] = {}
    for label, lo, hi in buckets:
        cond = np.ones_like(values, dtype=bool)
        if lo is not None:
            cond &= values >= lo
        if hi is not None:
            cond &= values < hi
        out[label] = float(np.mean(cond))
    return out


def _build_tail_risk_table(
    total_shares: np.ndarray,
    cap_shares: Optional[float],
    forward_price: float,
    thresholds: Tuple[float, ...] = _DEFAULT_TAIL_PCTS,
) -> List[TailRiskRow]:
    """Build the canonical buy-side tail-risk table.

    If max_total_shares is known, thresholds are taken as a fraction of cap.
    If cap is None / inf, we fall back to using the empirical p95 as the
    'effective ceiling' (capped at observed max).
    """
    if cap_shares is None or not np.isfinite(cap_shares):
        cap_for_thresholds = float(np.percentile(total_shares, 95))
    else:
        cap_for_thresholds = float(cap_shares)

    rows: List[TailRiskRow] = []
    for pct in thresholds:
        thr_shares = pct * cap_for_thresholds
        prob = float(np.mean(total_shares >= thr_shares))
        cash = thr_shares * forward_price
        rows.append(TailRiskRow(
            threshold_pct_of_cap=float(pct),
            threshold_shares=float(thr_shares),
            probability=prob,
            cash_outlay_at_strike=cash,
        ))
    return rows


def _sample_paths_for_plot(
    spots: np.ndarray,
    ko_idx: np.ndarray,
    k: int = 100,
    seed: int = 1729,
) -> Tuple[np.ndarray, np.ndarray]:
    """Pick k representative paths: mix of KO and non-KO so the chart shows both.

    Half are random KO paths, half are random no-KO paths (if available).
    """
    rng = np.random.default_rng(seed)
    n = spots.shape[0]
    k = min(k, n)
    ko_paths = np.where(ko_idx >= 0)[0]
    no_ko_paths = np.where(ko_idx < 0)[0]

    half = k // 2
    pick: List[int] = []
    if len(ko_paths) > 0:
        pick.extend(rng.choice(ko_paths, size=min(half, len(ko_paths)), replace=False).tolist())
    remaining = k - len(pick)
    pool = no_ko_paths if len(no_ko_paths) > 0 else np.arange(n)
    pick.extend(rng.choice(pool, size=min(remaining, len(pool)), replace=False).tolist())

    pick = np.asarray(pick, dtype=int)
    return spots[pick], ko_idx[pick]


def _simulate_spots(
    *,
    mkt: EquitySnapshot,
    times: np.ndarray,
    T: float,
    settings: MCSettings,
    spot: Optional[float] = None,
    vol: Optional[float] = None,
    parallel_vol_bump: float = 0.0,
) -> np.ndarray:
    """Generate paths using flat vol or the snapshot's smile/local-vol surface."""
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
        S0=S0,
        times=times,
        r_minus_q=drift,
        sigma=sigma,
        n_paths=settings.n_paths,
        seed=settings.seed,
    )


# --------------------------------------------------------------------- #
# Greeks helper (lifted out of mc.py so we can reuse with custom drift)
# --------------------------------------------------------------------- #

def _compute_greeks(
    pv: float,
    mkt: EquitySnapshot,
    times: np.ndarray,
    terms,
    settings: MCSettings,
    rt: AQDQRuntimeState,
    gtd_rem: int,
    lump_idx: Optional[int],
    settlement_mode: str,
    T: float,
) -> Dict[str, float]:
    """Finite-difference Delta / Gamma / Vega with common random numbers."""
    bump_S = settings.spot_bump
    bump_v = settings.vol_bump
    side_sign = -1.0 if terms.side == "sell" else 1.0

    def reprice(spot=None, vol=None, vol_bump: float = 0.0) -> float:
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
            delivered_to_date=rt.delivered_to_date, side=terms.side,
            lnbd_dir=terms.lnbd_direction,
            aq_mode=getattr(terms, "aq_mode", "regular"),
            gtd_lump_index_in_remaining=lump_idx,
            enable_pnbd=getattr(terms, "enable_pnbd", True),
        )
        if settlement_mode == "daily":
            dfs = np.array([mkt.df(t) for t in times], dtype=float)
            return side_sign * float(np.mean(((sp - terms.forward_price) * dsh) @ dfs))
        else:
            return side_sign * float(np.mean(mkt.df(T) * (ST - terms.forward_price) * sh))

    pv_up = reprice(spot=mkt.spot * (1 + bump_S))
    pv_dn = reprice(spot=mkt.spot * (1 - bump_S))
    dS = mkt.spot * (1 + bump_S) - mkt.spot * (1 - bump_S)
    delta = (pv_up - pv_dn) / dS
    gamma = (pv_up - 2 * pv + pv_dn) / ((0.5 * dS) ** 2)

    if getattr(mkt, "vol_surface", None) is not None:
        pv_vup = reprice(vol_bump=bump_v)
        pv_vdn = reprice(vol_bump=-bump_v)
    else:
        pv_vup = reprice(vol=mkt.vol + bump_v)
        pv_vdn = reprice(vol=max(1e-6, mkt.vol - bump_v))
    vega = (pv_vup - pv_vdn) / (2 * bump_v)

    return {"delta": float(delta), "gamma": float(gamma), "vega": float(vega)}


def _normalize_greeks(
    greeks: Dict[str, float],
    *,
    spot: float,
    cap_shares: Optional[float],
    strike: float,
) -> Dict[str, float]:
    """Express the raw Greeks as cash amounts and as % of the trade notional.

    DEALER NOTE: desks rarely look at raw Greeks alone — they read them next to
    the position's notional so risk is comparable across trades of different
    sizes. We report two normalisers because both are meaningful for an
    accumulator whose strike sits well below spot:
      - notional @ strike = cap_shares * strike  ("full-size" cost if the trade
        runs to the cap and every share is bought at the contractual strike)
      - notional @ spot   = cap_shares * spot     ("full-size" at today's price)

    Conventions:
      - delta_cash      = delta_shares * spot            ($ equity exposure)
      - vega_cash       = vega                           (already $ per vol pt)
      - gamma_cash_1pct = gamma * spot**2 / 100          (standard "dollar gamma":
        the change in delta_cash for a 1% spot move)
    Each is then divided by both notionals to give a percentage.
    """
    out: Dict[str, float] = {}
    if cap_shares is None or not np.isfinite(cap_shares) or cap_shares <= 0:
        # No cap → percentages are not well-defined; report cash only.
        notional_strike = float("nan")
        notional_spot = float("nan")
    else:
        notional_strike = float(cap_shares) * float(strike)
        notional_spot = float(cap_shares) * float(spot)

    out["spot"] = float(spot)
    out["notional_at_strike"] = notional_strike
    out["notional_at_spot"] = notional_spot

    delta_cash = greeks.get("delta", float("nan")) * spot
    vega_cash = greeks.get("vega", float("nan"))
    gamma_cash_1pct = greeks.get("gamma", float("nan")) * (spot ** 2) / 100.0

    out["delta_cash"] = float(delta_cash)
    out["vega_cash"] = float(vega_cash)
    out["gamma_cash_1pct"] = float(gamma_cash_1pct)

    for label, cash in (("delta", delta_cash), ("vega", vega_cash), ("gamma", gamma_cash_1pct)):
        out[f"{label}_pct_strike"] = float(cash / notional_strike) if np.isfinite(notional_strike) else float("nan")
        out[f"{label}_pct_spot"] = float(cash / notional_spot) if np.isfinite(notional_spot) else float("nan")

    return out


# --------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------- #

def run_scenario_analysis(
    *,
    terms,
    schedule,
    mkt: EquitySnapshot,
    rt: AQDQRuntimeState,
    settings: MCSettings,
    settlement_mode: str = "daily",
    scenario_name: str = "risk_neutral",
    sample_size_for_plots: int = 100,
    compute_greeks: bool = True,
) -> ScenarioResult:
    """Run one MC scenario and return all aggregated risk metrics.

    The scenario name is informational. The actual macro view is encoded in
    `mkt.drift_override` (set by `mkt.with_drift_override(...)`); if None,
    we run risk-neutral.

    Greeks are only computed when compute_greeks=True. For bull/bear scenarios
    they are usually skipped because Greeks have no meaning outside the
    risk-neutral measure.
    """
    # --- time axis ---
    obs_rem = schedule.remaining_observation_dates(terms, rt.today, include_today_close=False)
    if not obs_rem:
        raise ValueError("No remaining observation dates — trade has matured.")
    times = yearfractions(schedule.dc, rt.today, obs_rem)
    T = times[-1]

    gtd_rem = schedule.gtd_days_remaining(terms, rt.today, include_today_close=False)
    lump_idx = None
    if getattr(terms, "aq_mode", "regular") == "speedy":
        if len(obs_rem) >= 2 and gtd_rem > 0:
            lump_idx = 1

    # --- path simulation ---
    drift_used = mkt.fwd_drift(t=T)
    spots = _simulate_spots(
        mkt=mkt, times=times, T=T, settings=settings,
    )
    S_T = spots[:, -1]

    # --- per-path shares & KO ---
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
        enable_pnbd=getattr(terms, "enable_pnbd", True),
    )

    # --- valuation (risk-neutral interpretation if drift_override is None) ---
    if settlement_mode == "daily":
        dfs = np.array([mkt.df(t) for t in times], dtype=float)
        payoff_mat = (spots - terms.forward_price) * daily_shares
        pv_paths = payoff_mat @ dfs
        cash_outlay_paths = (terms.forward_price * daily_shares) @ dfs
    elif settlement_mode == "final":
        pv_paths = mkt.df(T) * (S_T - terms.forward_price) * total_shares
        cash_outlay_paths = mkt.df(T) * terms.forward_price * total_shares
    else:
        raise ValueError("settlement_mode must be 'final' or 'daily'")

    # Sign for DQ (sell) — payoff is reversed. Here we follow the engine's
    # existing convention: shares are non-negative magnitudes, and the
    # outer engine applies the side sign when aggregating book P&L.
    if terms.side == "sell":
        pv_paths = -pv_paths

    pv = float(np.mean(pv_paths))
    std_err = float(np.std(pv_paths, ddof=1) / np.sqrt(settings.n_paths))

    # --- Greeks ---
    if compute_greeks:
        greeks = _compute_greeks(
            pv=pv, mkt=mkt, times=times, terms=terms, settings=settings,
            rt=rt, gtd_rem=gtd_rem, lump_idx=lump_idx,
            settlement_mode=settlement_mode, T=T,
        )
    else:
        greeks = {"delta": float("nan"), "gamma": float("nan"), "vega": float("nan")}

    # Normalized Greeks (cash + % of notional). Only meaningful when Greeks
    # were actually computed (risk-neutral); skip for bull/bear scenarios.
    greeks_normalized = (
        _normalize_greeks(
            greeks, spot=mkt.spot,
            cap_shares=terms.max_total_shares, strike=terms.forward_price,
        )
        if compute_greeks else {}
    )

    # --- KO statistics ---
    ko_prob = float(np.mean(ko_idx >= 0))
    ko_dist = _ko_timing_distribution(ko_idx, n_steps=len(obs_rem))
    if np.any(ko_idx >= 0):
        ko_only = ko_idx[ko_idx >= 0]
        expected_ko = float(np.mean(ko_only))
        median_ko: Optional[int] = int(np.median(ko_only))
    else:
        expected_ko = None
        median_ko = None

    # --- share distribution ---
    expected_shares = float(np.mean(total_shares))
    median_shares = float(np.median(total_shares))
    p5 = float(np.percentile(total_shares, 5))
    p95 = float(np.percentile(total_shares, 95))
    p99 = float(np.percentile(total_shares, 99))
    max_shares = float(np.max(total_shares))
    shares_hist = _histogram(total_shares, bins=30)

    # --- tail risk table ---
    tail = _build_tail_risk_table(
        total_shares=total_shares,
        cap_shares=terms.max_total_shares,
        forward_price=terms.forward_price,
    )

    # --- terminal MTM distribution ---
    # MTM PV per path: we already have it in pv_paths.
    expected_mtm = pv
    median_mtm = float(np.median(pv_paths))
    p5_mtm = float(np.percentile(pv_paths, 5))
    p95_mtm = float(np.percentile(pv_paths, 95))
    prob_loss = float(np.mean(pv_paths < 0))

    # Client-facing P&L return: P&L as % of strike cash paid for accumulated
    # shares. This is different from PV / maximum cap notional; it answers the
    # investor's "what return did I make on the shares I was forced/allowed to
    # buy?" question.
    pnl_return = np.divide(
        pv_paths,
        cash_outlay_paths,
        out=np.zeros_like(pv_paths, dtype=float),
        where=(cash_outlay_paths > 0),
    )
    expected_pnl_return = float(np.mean(pnl_return))
    median_pnl_return = float(np.median(pnl_return))
    p5_pnl_return = float(np.percentile(pnl_return, 5))
    p95_pnl_return = float(np.percentile(pnl_return, 95))
    pnl_buckets = _pnl_return_buckets(pnl_return)

    # --- representative paths for plotting ---
    sample_paths, sample_ko_idx = _sample_paths_for_plot(
        spots=spots, ko_idx=ko_idx, k=sample_size_for_plots,
    )

    return ScenarioResult(
        scenario_name=scenario_name,
        drift_used=drift_used,
        pv=pv,
        std_err=std_err,
        greeks=greeks,
        greeks_normalized=greeks_normalized,
        ko_probability=ko_prob,
        ko_timing_distribution=ko_dist,
        expected_ko_period=expected_ko,
        median_ko_period=median_ko,
        expected_shares=expected_shares,
        median_shares=median_shares,
        p5_shares=p5,
        p95_shares=p95,
        p99_shares=p99,
        max_shares=max_shares,
        shares_histogram=shares_hist,
        tail_risk_table=tail,
        expected_mtm=expected_mtm,
        median_mtm=median_mtm,
        p5_mtm=p5_mtm,
        p95_mtm=p95_mtm,
        prob_terminal_loss=prob_loss,
        expected_pnl_return=expected_pnl_return,
        median_pnl_return=median_pnl_return,
        p5_pnl_return=p5_pnl_return,
        p95_pnl_return=p95_pnl_return,
        pnl_return_buckets=pnl_buckets,
        sample_paths=sample_paths,
        sample_path_ko_idx=sample_ko_idx,
        obs_dates=obs_rem,
        times_yr=times,
        n_paths=settings.n_paths,
        settlement_mode=settlement_mode,
        underlying=getattr(terms, "ticker", "") or "",
        forward_price=float(terms.forward_price),
        ko_level=float(terms.ko_level),
        notional_cap_shares=(float(terms.max_total_shares) if terms.max_total_shares is not None else None),
    )
