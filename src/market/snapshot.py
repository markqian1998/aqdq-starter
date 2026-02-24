# src/market/snapshot.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Optional, Tuple
import math
import QuantLib as ql

from src.market.feeds.prices import get_spot_div_snapshot, to_yf_symbol
from src.market.feeds.hibor_tma import fetch_hibor_today_tma, build_discount_curve_from_tma_deposits
from src.market.feeds.sofr_curve import get_default_usd_sofr_curve


# ---------- Helper: infer market from ticker ----------
def infer_market(ticker: str, exchange_hint: Optional[str] = None) -> str:
    """Return 'US' or 'HK' based on exchange_hint or ticker suffix.

    Priority:
      1. Use exchange_hint if provided.
      2. Detect HK from ticker suffix (.HK / ' HK' / 4-5 digit numeric code).
      3. Default to 'US' if not recognised.
    """
    if exchange_hint:
        h = exchange_hint.strip().upper()
        if h in ("HK", "HKG", "HKEX"):
            return "HK"
        if h in ("US", "NYSE", "NASDAQ"):
            return "US"

    t = ticker.strip().upper()
    if t.endswith(".HK") or t.endswith(" HK"):
        return "HK"
    # Four-to-five digit numeric codes are typical HK exchange tickers (e.g. "0981 HK")
    core = t.replace(".HK", "").replace(" HK", "").strip()
    if core.isdigit() and 4 <= len(core) <= 5:
        return "HK"
    return "US"


@dataclass
class EquitySnapshot:
    """Container for all market inputs required by the MC pricing engine."""
    today: date
    spot: float
    vol: float
    div_yield: float = 0.0                  # continuous dividend yield q
    discount_curve: Optional[ql.YieldTermStructureHandle] = None
    curve_ccy: str = "USD"                  # currency of the discount curve
    market: str = "US"                      # 'US' or 'HK'
    flat_r: float = 0.0                     # fallback flat rate when no curve is available

    def df(self, t: float) -> float:
        """Return the discount factor for year-fraction t.

        Uses the discount curve when available; falls back to exp(-flat_r * t).
        """
        if self.discount_curve is not None:
            try:
                return float(self.discount_curve.discount(t))
            except Exception:
                pass
        return math.exp(-self.flat_r * t)

    def fwd_drift(self, t: float | None = None) -> float:
        """Return the risk-neutral drift r - q.

        If a discount curve is available and t is provided, r is derived from
        the zero rate at that maturity; otherwise flat_r is used.
        """
        r = self.flat_r
        if self.discount_curve is not None and (t is not None) and (t > 0.0):
            try:
                zr = self.discount_curve.zeroRate(t, ql.Continuous, ql.NoFrequency)
                r = float(zr.rate())
            except Exception:
                pass
        return r - self.div_yield

    @classmethod
    def flat(cls, today: date, spot: float, vol: float, r: float, q: float,
             market: str = "US", curve_ccy: str = "USD") -> "EquitySnapshot":
        """Convenience constructor for a flat-rate snapshot (no term structure)."""
        return cls(today=today, spot=spot, vol=vol, div_yield=q,
                   discount_curve=None, curve_ccy=curve_ccy, market=market, flat_r=r)


# ---------- Factory: auto-build snapshot with live curves ----------
def build_snapshot_auto(
    *,
    ticker: str,
    today: date,
    vol: float,
    exchange_hint: Optional[str] = None,
    override_curve: Optional[ql.YieldTermStructureHandle] = None,
    override_div_yield: Optional[float] = None,
    spot_override: Optional[float] = None,
) -> EquitySnapshot:
    """Build an EquitySnapshot by auto-detecting market and loading the appropriate curve.

    Steps:
      1. Infer exchange (US / HK) from ticker or exchange_hint.
      2. Fetch spot price and dividend yield (via prices.py / yfinance).
      3. Load discount curve: US -> SOFR; HK -> HIBOR (TMA).
      4. Return a ready-to-use EquitySnapshot for the MC engine.
    """
    mkt = infer_market(ticker, exchange_hint)
    px  = get_spot_div_snapshot(ticker, today)
    spot = spot_override if spot_override is not None else px.spot
    div  = override_div_yield if override_div_yield is not None else px.div_yield

    curve_ccy = "USD" if mkt == "US" else "HKD"
    curve  = None
    flat_r = 0.0

    if override_curve is not None:
        curve = override_curve
    else:
        if mkt == "US":
            # US market: load SOFR OIS discount curve
            try:
                curve = get_default_usd_sofr_curve(today)
            except Exception:
                curve  = None
                flat_r = 0.04  # flat-rate fallback if SOFR bootstrap fails
        else:
            # HK market: load HIBOR curve from TMA live fixing
            try:
                row   = fetch_hibor_today_tma()   # pandas.Series of deposit rates (decimal)
                curve = build_discount_curve_from_tma_deposits(today, row)
            except Exception:
                curve  = None
                flat_r = 0.03  # flat-rate fallback if TMA fetch fails

    return EquitySnapshot(
        today=today, spot=spot, vol=vol,
        div_yield=float(div or 0.0),
        discount_curve=curve, curve_ccy=curve_ccy, market=mkt,
        flat_r=flat_r
    )
