# src/engines/payoff.py
"""Path-wise share accumulation and knock-out logic for AQ/DQ structured products.

For each simulated price path and each observation date, this module applies
the following product rules:
  - Did the path knock out (KO) today?
  - How many shares are delivered today (1x or gear x via LNBD)?
  - If KO occurs within the GTD window, deliver remaining GTD shares as a
    lump sum (PNBD) and stop further accumulation.

The output (total shares per path, KO day per path, daily share matrix) feeds
directly into the Monte Carlo pricing engine, which discounts the resulting
cash flows back to today.

Note: this function deals only with "rules -> shares". Cash flows are computed
in the outer MC engine by multiplying shares by (S_t - K) and discounting.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Literal, Optional
from datetime import date
import numpy as np

Side = Literal["buy", "sell"]
Dir  = Literal["above", "below"]


@dataclass
class AQDQRuntimeState:
    """Mutable state at the valuation date.

    delivered_to_date tracks shares already physically or cash-settled before
    today and is used to deduct from the remaining cap.
    """
    today: date
    delivered_to_date: float = 0.0


def _lnbd_multiplier(
    side: Side, lnbd_dir: Dir, spot: np.ndarray, strike: float, gear: float
) -> np.ndarray:
    """Return the per-path daily multiplier (1 or gear) based on the LNBD rule.

    LNBD (Low/No Boundary Doubling): when the spot is on the qualifying side
    of the forward price, the daily accumulation is multiplied by gear (typically 2x).
    For a standard AQ: lnbd_dir='below' means gear applies when spot <= strike.
    """
    cond = (spot <= strike) if lnbd_dir == "below" else (spot >= strike)
    return np.where(cond, gear, 1.0)   # returns a vector: one value per live path


def pathwise_shares_and_ko(
    *,
    spots: np.ndarray,                 # (n_paths, n_steps) price matrix
    S_T: np.ndarray,                   # (n_paths,) terminal prices (unused internally; kept for API symmetry)
    forward_price: float,
    ko_level: float,
    ko_dir: str,                       # "above" | "below"
    shares_per_day: float,
    gear_ratio: float,
    gtd_days_remaining: int,           # GTD days remaining as of the valuation date
    max_total_shares: Optional[float],
    delivered_to_date: float,
    side: str,                         # "buy" | "sell" (sign convention applied in the outer engine)
    lnbd_dir: str,                     # "below" | "above"  (typically "below" for AQ)
    aq_mode: str = "regular",          # "regular" or "speedy"
    gtd_lump_index_in_remaining: Optional[int] = None  # speedy: index in remaining obs dates for lump grant (typically 1)
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute per-path share accumulation and KO events.

    Returns
    -------
    total_shares : (n_paths,)
        Total shares accumulated by each path (net of cap and delivered_to_date).
    ko_index : (n_paths,)
        Observation-date index at which each path knocked out; -1 if no KO.
    daily_shares : (n_paths, n_steps)
        Daily share matrix used for daily-settlement discounting.

    Settlement modes
    ----------------
    regular AQ:
        - Daily accumulation at 1x or gear x (LNBD) during and after GTD.
        - KO within GTD  -> deliver remaining GTD days at 1x (PNBD), then stop.
        - KO after  GTD  -> stop accumulation from KO day onwards.

    speedy AQ:
        - On day gtd_lump_index_in_remaining (typically day 2), deliver all
          remaining GTD days at 1x in a single lump sum.
        - No further daily accumulation during GTD (avoids double counting).
        - KO within GTD  -> product terminates (lump already granted is kept).
        - KO after  GTD  -> stop accumulation from KO day onwards.
    """
    n_paths, n_steps = spots.shape
    daily = np.zeros((n_paths, n_steps), dtype=float)
    ko_index = np.full(n_paths, -1, dtype=int)

    cap0 = np.inf if max_total_shares is None else max(0.0, max_total_shares - delivered_to_date)
    remaining_cap = np.full(n_paths, cap0, dtype=float)

    def ko_hit(s, level, direction):
        return (s >= level) if direction == "above" else (s <= level)

    def lnbd_mult(spot, K, gear, direction):
        cond = (spot <= K) if direction == "below" else (spot >= K)
        return np.where(cond, gear, 1.0)

    if aq_mode == "speedy":
        # Step 1: Lump-sum GTD grant on the designated day
        if gtd_days_remaining > 0 and gtd_lump_index_in_remaining is not None \
           and 0 <= gtd_lump_index_in_remaining < n_steps:
            i_lump = int(gtd_lump_index_in_remaining)
            grant_qty = shares_per_day * gtd_days_remaining
            if grant_qty > 0:
                alive = (remaining_cap > 0)
                if np.any(alive):
                    add = np.minimum(grant_qty, remaining_cap[alive])
                    daily[alive, i_lump] += add
                    remaining_cap[alive] -= add

        # Step 2: Daily loop — no accumulation during GTD; LNBD accumulation after GTD until KO
        for i in range(n_steps):
            s_i = spots[:, i]

            # Check for KO
            alive = (ko_index < 0)
            hit = np.zeros(n_paths, dtype=bool)
            hit[alive] = ko_hit(s_i[alive], ko_level, ko_dir)
            ko_index[(ko_index < 0) & hit] = i

            # Skip daily accumulation during the GTD window (lump already granted above)
            if i < gtd_days_remaining:
                continue

            alive_after = (ko_index < 0) & (remaining_cap > 0)
            if np.any(alive_after):
                mult = lnbd_mult(s_i[alive_after], forward_price, gear_ratio, lnbd_dir)
                want = shares_per_day * mult
                add = np.minimum(want, remaining_cap[alive_after])
                daily[alive_after, i] += add
                remaining_cap[alive_after] -= add

    else:  # regular
        for i in range(n_steps):
            s_i = spots[:, i]

            # Allocate today's shares to all paths that have not yet knocked out
            alive_for_alloc = (ko_index < 0) & (remaining_cap > 0)
            if np.any(alive_for_alloc):
                mult = lnbd_mult(s_i[alive_for_alloc], forward_price, gear_ratio, lnbd_dir)
                want = shares_per_day * mult
                add = np.minimum(want, remaining_cap[alive_for_alloc])
                daily[alive_for_alloc, i] += add
                remaining_cap[alive_for_alloc] -= add

            # Check for KO
            alive = (ko_index < 0)
            hit = np.zeros(n_paths, dtype=bool)
            hit[alive] = ko_hit(s_i[alive], ko_level, ko_dir)
            ko_index[(ko_index < 0) & hit] = i

            # PNBD: if KO occurs within GTD, deliver remaining GTD days at 1x (lump)
            if np.any(hit) and i < gtd_days_remaining:
                rem_idx = np.arange(i + 1, min(gtd_days_remaining, n_steps))
                if rem_idx.size > 0:
                    hit_paths = np.where(hit)[0]
                    for j in rem_idx:
                        still_room = (remaining_cap[hit_paths] > 0)
                        if np.any(still_room):
                            idx = hit_paths[still_room]
                            add = np.minimum(shares_per_day, remaining_cap[idx])
                            daily[idx, j] += add
                            remaining_cap[idx] -= add
                # Paths that knocked out will not accumulate further (ko_index blocks them).

    total_shares = daily.sum(axis=1)
    return total_shares, ko_index, daily

# Settlement mode note:
#
# "final"  (single settlement at maturity):
#     PV = DF(T) * (S_T - K) * total_shares
#
# "daily"  (mark-to-market each observation day):
#     PV = sum_i [ DF(t_i) * (S_{t_i} - K) * daily_shares[:, i] ]
#
# The outer MC engine (mc.py) handles both modes using the daily_shares matrix.
