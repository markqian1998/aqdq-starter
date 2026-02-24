# src/market/feeds/sofr_curve.py
"""Build a USD SOFR OIS discount curve from Bloomberg horizon-curve exports or default pillars.

Public API
----------
read_bbg_horizon_excel(path)
    Parse a Bloomberg "Horizon Curve / Spot(%)" Excel or CSV export (FWCV screen)
    and return a list of (tenor_str, rate_decimal) tuples.

build_sofr_discount_from_swaps(today, swap_pillars)
    Bootstrap a QuantLib YieldTermStructureHandle from USD OIS par swap rates.

get_default_usd_sofr_curve(today)
    Convenience wrapper that bootstraps using built-in representative SOFR pillars.
    Replace DEFAULT_SOFR_PILLARS with live Bloomberg data for production use.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Tuple, List, Optional

import pandas as pd
import QuantLib as ql


# =========================================================
# Read a Bloomberg "Horizon Curve / Spot(%)" table (FWCV)
# Expected columns: Tenor | Spot | 1 Mo | 3 Mo | ...
# We use the "Spot" column (OIS fixed-leg par rate).
# =========================================================

def read_bbg_horizon_excel(path: str, sheet_name: int | str = 0) -> List[Tuple[str, float]]:
    """Parse a Bloomberg Horizon Curve Excel or CSV export.

    Automatically locates columns whose names contain 'tenor' and 'spot'
    (case-insensitive). Converts percentage values to decimals (e.g. 4.268 -> 0.04268).

    Parameters
    ----------
    path       : path to an .xlsx or .csv file
    sheet_name : sheet index or name (Excel only; ignored for CSV)

    Returns
    -------
    List of (tenor_string, rate_decimal) tuples, sorted by maturity.
    """
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path, sheet_name=sheet_name)

    tenor_col = next(c for c in df.columns if "tenor" in str(c).lower())
    spot_col  = next(c for c in df.columns if "spot"  in str(c).lower())

    out: List[Tuple[str, float]] = []
    for _, row in df.iterrows():
        ten = str(row[tenor_col]).strip()
        if not ten or ten.lower() == "nan":
            continue
        v = row[spot_col]
        if pd.isna(v):
            continue
        r = float(v)
        if r > 1.0:         # Bloomberg reports rates as percentages (e.g. 4.268)
            r /= 100.0
        out.append((ten, r))

    def _key(x: Tuple[str, float]):
        ten = x[0].lower().replace(" ", "")
        if ten.endswith("mo") or ten.endswith("m"):
            n = int(ten.replace("mo", "").replace("m", ""))
            return (0, n)
        if ten.endswith("yr") or ten.endswith("y"):
            n = int(ten.replace("yr", "").replace("y", ""))
            return (1, n)
        return (9, 9999)

    out.sort(key=_key)
    return out


# =========================================================
# Tenor string -> QuantLib Period
# Supports: "1 Mo", "3 Mo", "1 Yr", "2 Yr", "50 Yr", etc.
# =========================================================

def _to_period(tenor_str: str) -> ql.Period:
    s = tenor_str.strip().lower().replace(" ", "")
    if s.endswith("mo") or s.endswith("m"):
        n = int(s.replace("mo", "").replace("m", ""))
        return ql.Period(n, ql.Months)
    if s.endswith("yr") or s.endswith("y"):
        n = int(s.replace("yr", "").replace("y", ""))
        return ql.Period(n, ql.Years)
    if s in ("on", "overnight"):
        return ql.Period(1, ql.Days)
    if s.endswith("w") or s.endswith("wk"):
        n = int(s.replace("wk", "").replace("w", ""))
        return ql.Period(n, ql.Weeks)
    raise ValueError(f"Unrecognised tenor string: {tenor_str!r}")


# =========================================================
# Bootstrap a SOFR OIS discount curve from par swap rates
#
# Mathematical intuition:
#   For each maturity T, the OIS swap satisfies:
#       PV(fixed leg) = PV(floating leg) ≈ 1 - DF(T)
#   We solve for DF at each maturity sequentially —
#   this is "bootstrapping": short-end rates anchor the
#   near-term discount factors, which are then used to
#   strip further maturities iteratively.
# =========================================================

def build_sofr_discount_from_swaps(
    *,
    today: date,
    swap_pillars: Iterable[Tuple[str, float]],   # [(tenor, par_rate_decimal), ...]
    calendar: ql.Calendar = ql.UnitedStates(ql.UnitedStates.FederalReserve),
    settlement_days: int = 2,                    # standard T+2 for USD OIS
    fixed_leg_daycount: ql.DayCounter = ql.Actual360(),
    fixed_leg_bdc: ql.BusinessDayConvention = ql.ModifiedFollowing,
    piecewise_cls=ql.PiecewiseLogLinearDiscount,  # log-linear interpolation on DF ensures positive values
) -> ql.YieldTermStructureHandle:
    """Bootstrap a USD SOFR OIS discount curve from par swap rates.

    Parameters
    ----------
    today         : valuation / evaluation date
    swap_pillars  : iterable of (tenor_string, par_rate_decimal) tuples
    piecewise_cls : QuantLib interpolation class (default: PiecewiseLogLinearDiscount)

    Returns
    -------
    ql.YieldTermStructureHandle ready for use in EquitySnapshot.
    """
    ql_today = ql.Date(today.day, today.month, today.year)
    ql.Settings.instance().evaluationDate = ql_today

    sofr = ql.Sofr()   # built-in SOFR overnight index (OIS floating leg)

    helpers: List[ql.RateHelper] = []
    for ten, r in swap_pillars:
        quote  = ql.QuoteHandle(ql.SimpleQuote(float(r)))
        tenor  = _to_period(ten)
        helpers.append(
            ql.OISRateHelper(
                settlement_days, tenor, quote, sofr,
                fixed_leg_daycount, fixed_leg_bdc
            )
        )

    if not helpers:
        raise RuntimeError("No OIS swap pillars provided for bootstrapping")

    curve = piecewise_cls(ql_today, helpers, fixed_leg_daycount)
    return ql.YieldTermStructureHandle(curve)


# =========================================================
# Diagnostic: sample DF / zero rate / instantaneous forward
# =========================================================

def sample_curve(handle: ql.YieldTermStructureHandle, times: List[float]) -> pd.DataFrame:
    """Sample discount factors, zero rates, and instantaneous forwards from a curve."""
    out = []
    for T in times:
        df  = float(handle.discount(T))
        z   = -(ql.log(df) / T) if T > 0 else 0.0   # continuous zero rate: z = -ln(DF)/T
        dt  = 1e-4
        df2 = float(handle.discount(T + dt))
        fwd = -(ql.log(df2) - ql.log(df)) / dt       # instantaneous forward (finite difference)
        out.append({"T": T, "DF": df, "Zero(c)": z, "InstFwd(c)": fwd})
    return pd.DataFrame(out)


# =========================================================
# Default USD SOFR curve (bootstrapped from representative pillars)
# =========================================================

def get_default_usd_sofr_curve(today: date) -> ql.YieldTermStructureHandle:
    """Build and return a USD SOFR OIS discount curve bootstrapped from representative
    par swap rates (approximate early-2025 levels).

    In production you should replace DEFAULT_SOFR_PILLARS with live data from
    Bloomberg (FWCV screen, 'Spot' column) or another market data provider,
    then call build_sofr_discount_from_swaps() directly with those pillars.

    Example (production):
        pillars = read_bbg_horizon_excel("sofr_ois_20250820.xlsx")
        curve   = build_sofr_discount_from_swaps(today=today, swap_pillars=pillars)
    """
    # Representative USD SOFR OIS par swap rates (approximate early-2025 levels).
    # Replace these with live Bloomberg data for production use.
    DEFAULT_SOFR_PILLARS: List[Tuple[str, float]] = [
        ("1M",  0.0433),
        ("3M",  0.0432),
        ("6M",  0.0427),
        ("1Y",  0.0415),
        ("2Y",  0.0400),
        ("3Y",  0.0390),
        ("5Y",  0.0385),
        ("7Y",  0.0385),
        ("10Y", 0.0388),
        ("15Y", 0.0395),
        ("20Y", 0.0398),
        ("30Y", 0.0400),
    ]
    try:
        return build_sofr_discount_from_swaps(today=today, swap_pillars=DEFAULT_SOFR_PILLARS)
    except Exception:
        # Last-resort fallback: flat curve to prevent crashes when QuantLib
        # bootstrapping fails (e.g. evaluation date outside curve range).
        ql_today = ql.Date(today.day, today.month, today.year)
        ql.Settings.instance().evaluationDate = ql_today
        flat = ql.FlatForward(ql_today, 0.04, ql.Actual365Fixed(), ql.Compounded, ql.Annual)
        return ql.YieldTermStructureHandle(flat)
