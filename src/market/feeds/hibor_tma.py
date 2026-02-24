# src/market/feeds/hibor_tma.py
"""Fetch HKD HIBOR fixings from the TMA benchmark page and bootstrap a discount curve.

Public API
----------
fetch_hibor_today_tma(asof)
    Download the TMA HIBOR history page and return the fixing for the given
    date (or the most recent available date) as a pandas Series in decimal form.
    Index: ['ON', '1W', '2W', '1M', '2M', '3M', '6M', '12M']

build_discount_curve_from_tma_deposits(today, hibor_row)
    Bootstrap the eight HIBOR deposit points into a QuantLib
    YieldTermStructureHandle for use in pricing / discounting.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Optional, Dict, List

import requests
import pandas as pd
import numpy as np
import QuantLib as ql
from io import StringIO

TMA_HIBOR_URL = "https://benchmark.tma.org.hk/benchmark/history/hkd-interest-settlement-rates"


# -------- A) Download and parse the TMA table --------

def _download_tma_hibor_html() -> str:
    """Fetch the TMA HIBOR history page (contains the latest several trading days).

    Note: the TMA page is for informational purposes and may be delayed.
    For production use, supplement or replace with a licensed data source.
    """
    resp = requests.get(TMA_HIBOR_URL, timeout=15)
    resp.raise_for_status()
    return resp.text


def _parse_tma_hibor_table(html: str) -> pd.DataFrame:
    """Parse the HTML table from the TMA page into a wide DataFrame.

    Output format: first column = Tenor (ON/1W/…), remaining columns = one per date.
    """
    try:
        tables = pd.read_html(StringIO(html), flavor="lxml")
    except ValueError:
        # Fall back to the default parser if lxml is not installed
        tables = pd.read_html(StringIO(html))

    df = tables[0].copy()
    df.columns = [str(c).strip() for c in df.columns]

    # Normalise tenor labels to a consistent short form
    tenor_map = {
        "ON": "ON", "OVERNIGHT": "ON",
        "1WK": "1W", "1W": "1W",
        "2WK": "2W", "2W": "2W",
        "1M": "1M", "2M": "2M", "3M": "3M", "6M": "6M", "12M": "12M",
    }
    df.iloc[:, 0] = df.iloc[:, 0].map(
        lambda x: tenor_map.get(str(x).strip().upper(), str(x).strip().upper())
    )
    return df


def _latest_column_for_asof(df: pd.DataFrame, asof: Optional[date]) -> str:
    """Return the column name (date) to use for the given asof date.

    If asof is None, returns the leftmost (most recent) date column.
    Otherwise returns the closest column that is on or before asof.
    Dates in the page header are expected in dd/MM/yyyy format.
    """
    date_cols = [c for c in df.columns[1:]]   # skip the first Tenor column
    if not date_cols:
        raise RuntimeError("TMA page parse failed: no date columns found")
    if asof is None:
        return date_cols[0]

    def to_d(c):
        try:
            return pd.to_datetime(c, dayfirst=True).date()
        except Exception:
            return None

    pairs = [(c, to_d(c)) for c in date_cols]
    pairs = [(c, d) for (c, d) in pairs if d is not None]

    le_cols = [(c, d) for (c, d) in pairs if d <= asof]
    if le_cols:
        le_cols.sort(key=lambda x: x[1], reverse=True)
        return le_cols[0][0]

    # Fallback: closest column regardless of direction
    pairs.sort(key=lambda x: abs((x[1] - asof).days))
    return pairs[0][0]


def fetch_hibor_today_tma(asof: Optional[date] = None) -> pd.Series:
    """Return a Series of HIBOR fixings for the given date (or the most recent available).

    Returns
    -------
    pd.Series
        Index: ['ON', '1W', '2W', '1M', '2M', '3M', '6M', '12M']
        Values: rates as decimals (e.g. 2.87% -> 0.0287)
    """
    html = _download_tma_hibor_html()
    wide = _parse_tma_hibor_table(html)
    col  = _latest_column_for_asof(wide, asof)

    sub = wide[[wide.columns[0], col]].copy()
    sub.columns = ["tenor", "rate_pct"]
    sub["rate"] = pd.to_numeric(sub["rate_pct"], errors="coerce") / 100.0  # percent -> decimal

    order = ["ON", "1W", "2W", "1M", "2M", "3M", "6M", "12M"]
    out   = sub.set_index("tenor").reindex(order)["rate"]
    return out


# -------- B) Bootstrap a QuantLib discount curve from deposit rates --------

def build_discount_curve_from_tma_deposits(
    today: date,
    hibor_row: pd.Series,
    *,
    calendar: ql.Calendar = ql.HongKong(),
    daycounter: ql.DayCounter = ql.Actual365Fixed(),
    fixing_days: int = 2,
    bdc: ql.BusinessDayConvention = ql.Following,
    end_of_month: bool = False,
    piecewise_cls=ql.PiecewiseLogCubicDiscount,   # can be swapped for ql.PiecewiseLinearZero
) -> ql.YieldTermStructureHandle:
    """Bootstrap a HKD HIBOR discount curve from the eight TMA deposit points.

    Parameters
    ----------
    today       : valuation date
    hibor_row   : Series returned by fetch_hibor_today_tma() (decimal rates)
    piecewise_cls : QuantLib piecewise interpolation class (default: log-cubic discount)
    """
    ql_today = ql.Date(today.day, today.month, today.year)
    ql.Settings.instance().evaluationDate = ql_today

    helpers: List[ql.RateHelper] = []

    def add_dep(tenor: ql.Period, rate: float):
        if rate is None or not np.isfinite(rate):
            return
        quote = ql.QuoteHandle(ql.SimpleQuote(float(rate)))
        helpers.append(
            ql.DepositRateHelper(quote, tenor, fixing_days, calendar, bdc, end_of_month, daycounter)
        )

    # Map the eight TMA tenors to QuantLib Period objects
    ten_map: Dict[str, ql.Period] = {
        "ON":  ql.Period(1,  ql.Days),
        "1W":  ql.Period(1,  ql.Weeks),
        "2W":  ql.Period(2,  ql.Weeks),
        "1M":  ql.Period(1,  ql.Months),
        "2M":  ql.Period(2,  ql.Months),
        "3M":  ql.Period(3,  ql.Months),
        "6M":  ql.Period(6,  ql.Months),
        "12M": ql.Period(12, ql.Months),
    }
    for k, p in ten_map.items():
        if k in hibor_row.index:
            add_dep(p, hibor_row[k])

    if not helpers:
        raise RuntimeError("TMA HIBOR: insufficient deposit points for bootstrapping")

    curve = piecewise_cls(ql_today, helpers, daycounter)
    return ql.YieldTermStructureHandle(curve)
