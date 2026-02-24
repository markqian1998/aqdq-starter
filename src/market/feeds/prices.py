# src/market/feeds/prices.py
"""Fetch equity spot prices and dividend yields via yfinance.

Public API
----------
to_yf_symbol(ticker)
    Normalise an internal ticker string (e.g. "0981 HK", "AAPL US") to the
    yfinance symbol format (e.g. "0981.HK", "AAPL").

fetch_spot_yf(ticker)
    Return the most recent closing price for the given ticker.

fetch_dividend_yield_approx_yf(ticker)
    Return the trailing dividend yield as a decimal (e.g. 0.015 = 1.5%).

get_spot_div_snapshot(ticker, today)
    Convenience wrapper that returns both spot and dividend yield together.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Dict
import time

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None


# ---------- 1) Ticker normalisation ----------

def to_yf_symbol(ticker: str) -> str:
    """Convert an internal ticker to the yfinance symbol format.

    Examples
    --------
    'AAPL US'  -> 'AAPL'
    '0981 HK'  -> '0981.HK'   (zero-padded to 4 digits)
    '7203 JP'  -> '7203.T'

    Unrecognised formats are returned unchanged.
    """
    t = ticker.strip().upper()
    if t.endswith(" US"):
        return t[:-3]                          # 'AAPL US' -> 'AAPL'
    if t.endswith(" HK"):
        core = t[:-3].strip().zfill(4)        # '981 HK' -> '0981'
        return f"{core}.HK"
    if t.endswith(" JP"):
        return f"{t[:-3].strip()}.T"          # '7203 JP' -> '7203.T'
    # Extend here for other markets: '.KS', '.TW', '.SS', etc.
    return t


# ---------- 2) Spot price ----------

def fetch_spot_yf(ticker: str, max_retries: int = 3, sleep_s: float = 0.7) -> float:
    """Return the most recent closing price from yfinance.

    Retries up to max_retries times with a short sleep between attempts.
    Raises RuntimeError if all attempts fail.
    """
    if yf is None:
        raise RuntimeError("yfinance is not installed. Run: pip install yfinance")
    sym = to_yf_symbol(ticker)
    last_err: Optional[Exception] = None
    for _ in range(max_retries):
        try:
            df = yf.Ticker(sym).history(period="5d")
            if not df.empty and "Close" in df:
                return float(df["Close"].dropna().iloc[-1])
        except Exception as e:
            last_err = e
        time.sleep(sleep_s)
    raise RuntimeError(f"Failed to fetch spot for {ticker} ({sym}) from yfinance") from last_err


# ---------- 3) Dividend yield approximation ----------

def fetch_dividend_yield_approx_yf(ticker: str) -> float:
    """Return the trailing annual dividend yield as a decimal via yfinance.

    Uses the trailingAnnualDividendYield field from yfinance.info.
    Returns 0.0 if the data is unavailable or an error occurs.
    """
    if yf is None:
        return 0.0
    sym = to_yf_symbol(ticker)
    try:
        info = yf.Ticker(sym).info
        yld  = info.get("dividendYield", 0.0) or 0.0
        return float(yld)
    except Exception:
        return 0.0


# ---------- 4) Dividend table (advanced: for discrete dividend modelling) ----------

def fetch_dividend_table_yf(ticker: str) -> pd.DataFrame:
    """Return a historical dividend table with columns [ex_date, amount].

    Returns an empty DataFrame if no data is available.
    """
    if yf is None:
        return pd.DataFrame(columns=["ex_date", "amount"])
    sym = to_yf_symbol(ticker)
    try:
        s = yf.Ticker(sym).dividends   # Series(index=DatetimeIndex, values=float)
        if s is None or s.empty:
            return pd.DataFrame(columns=["ex_date", "amount"])
        out = s.reset_index()
        out.columns = ["ex_date", "amount"]
        out["ex_date"] = out["ex_date"].dt.date
        return out
    except Exception:
        return pd.DataFrame(columns=["ex_date", "amount"])


# ---------- 5) Combined snapshot ----------

@dataclass
class SpotDivSnapshot:
    """Spot price and dividend yield at a given valuation date."""
    today: date
    spot: float
    div_yield: float   # continuous-compounding approximation of the annualised dividend yield (decimal)


def get_spot_div_snapshot(ticker: str, today: Optional[date] = None) -> SpotDivSnapshot:
    """Fetch both spot price and dividend yield and return them as a SpotDivSnapshot."""
    today = today or date.today()
    s0 = fetch_spot_yf(ticker)
    q  = fetch_dividend_yield_approx_yf(ticker)
    return SpotDivSnapshot(today=today, spot=s0, div_yield=q)
