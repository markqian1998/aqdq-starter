# tests/test_payoff.py
import numpy as np
from datetime import date
from src.engines.payoff import pathwise_shares_and_ko

# ---- Simulated input (simplified example) ----
n_paths = 3   # three paths for easy inspection
n_steps = 5   # five observation dates

# Spot price matrix: (n_paths, n_steps)
spots = np.array([
    [50, 52, 54, 55, 56],   # path 1: always below KO level (59.1) -> no KO
    [50, 60, 61, 62, 63],   # path 2: breaches KO on day 2
    [50, 48, 47, 46, 45],   # path 3: always below strike (46.6) -> LNBD gear applies every day
])

# Terminal prices (required by API; not used internally by pathwise_shares_and_ko)
S_T = spots[:, -1]

# ---- Contract parameters (from example term sheet) ----
forward_price    = 46.6043
ko_level         = 59.1090
shares_per_day   = 855
gear_ratio       = 2.0
max_total_shares = 418950
delivered_to_date = 0.0
gtd_days_remaining = 37    # GTD days remaining as of valuation date
side             = "buy"
lnbd_dir         = "below"  # LNBD: gear applies when spot <= forward_price

# ---- Call the payoff engine ----
total_shares, ko_index, daily_shares = pathwise_shares_and_ko(
    spots=spots,
    S_T=S_T,
    forward_price=forward_price,
    ko_level=ko_level,
    ko_dir="above",
    shares_per_day=shares_per_day,
    gear_ratio=gear_ratio,
    gtd_days_remaining=gtd_days_remaining,
    max_total_shares=max_total_shares,
    delivered_to_date=delivered_to_date,
    side=side,
    lnbd_dir=lnbd_dir,
)

# ---- Print results ----
print("Total shares (per path):", total_shares)
print("KO day index  (per path):", ko_index)
