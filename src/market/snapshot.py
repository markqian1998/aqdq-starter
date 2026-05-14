# src/market/snapshot.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional, Tuple
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
    """Container for all market inputs required by the MC pricing engine.

    Drift convention (risk-neutral GBM):
        dS_t / S_t = (r - q - b) dt + sigma dW_t

    where b = borrow_spread_bps / 10_000 is the stock borrow / repo spread
    implied by the equity forward curve.

    On the role of b — read carefully, the textbooks often muddle this:

        The MARKET forward F = S * exp((r - q - b) * T) is what the dealer
        quotes against; b is whatever spread is needed to reconcile the
        observed forward with r and q. It is set by the equity-repo /
        stock-loan market and is the same b for both sides of any trade
        on that underlying — it is a property of the security, not the
        product or the side.

    Hedging interpretation (which side actually pays b in cash):

      - Accumulator (AQ, client BUYS): dealer is net SHORT delta and hedges
        by BUYING stock. The dealer is long the underlying and earns the
        dividend, pays funding. NO stock-borrow needed. b is small for the
        dealer's economics, but still flows through the forward used for
        pricing because it is a market-observable input.

      - Decumulator (DQ, client SELLS, also called "reverse accumulator"):
        dealer is net LONG delta and hedges by SHORTING stock. The dealer
        must borrow shares and pays the borrow rate b. This is where b
        becomes a material P&L line for the dealer (~25% of margin on
        hard-to-borrow names like HK small-caps with b = 200-500 bps).

    Practical guidance:
      - Liquid US large-caps (AVGO, NVDA, GOOGL, MSFT, etc.): b ≈ 0-5 bps,
        immaterial — set borrow_spread_bps=0.
      - Moderately tight US names: 25-100 bps.
      - HK small-caps and special situations: 200-500+ bps. Especially
        important for DQ trades.
      - Always set b from desk-quoted borrow when available; the broker
        usually has a "GC vs special" rate for each name.

    drift_override (optional): forces fwd_drift() to return this value
    regardless of curve / dividend / borrow. Used by scenario analysis
    (bull/bear/flat real-world drifts) where we want to run the MC under
    a non-risk-neutral assumption while keeping the discount curve intact.
    """
    today: date
    spot: float
    vol: float
    div_yield: float = 0.0                  # continuous dividend yield q
    discount_curve: Optional[ql.YieldTermStructureHandle] = None
    curve_ccy: str = "USD"                  # currency of the discount curve
    market: str = "US"                      # 'US' or 'HK'
    flat_r: float = 0.0                     # fallback flat rate when no curve is available
    borrow_spread_bps: float = 0.0          # stock borrow / repo spread in basis points (b)
    drift_override: Optional[float] = None  # real-world scenario override; bypasses r-q-b
    vol_surface: Optional[Any] = None        # optional smile/local-vol surface for path generation

    def df(self, t: float) -> float:
        """Return the discount factor for year-fraction t.

        Uses the discount curve when available; falls back to exp(-flat_r * t).
        Discounting always uses the risk-free curve, regardless of drift_override.
        (Discounting belongs to valuation; drift_override is for real-world simulation.)
        """
        if self.discount_curve is not None:
            try:
                return float(self.discount_curve.discount(t))
            except Exception:
                pass
        return math.exp(-self.flat_r * t)

    def fwd_drift(self, t: float | None = None) -> float:
        """Return the diffusion drift used by the GBM path generator.

        Risk-neutral default: r - q - b
            r derived from the discount curve at maturity t (if available),
            else flat_r.
            q = div_yield.
            b = borrow_spread_bps / 10_000.

        If drift_override is set (e.g. for a bullish/bearish real-world scenario)
        it is returned directly. The discount factor df() is unaffected — only
        the simulation drift changes. This is the correct way to run
        "what-if" macro views without contaminating the discounting.
        """
        if self.drift_override is not None:
            return float(self.drift_override)

        r = self.flat_r
        if self.discount_curve is not None and (t is not None) and (t > 0.0):
            try:
                zr = self.discount_curve.zeroRate(t, ql.Continuous, ql.NoFrequency)
                r = float(zr.rate())
            except Exception:
                pass
        b = self.borrow_spread_bps / 10_000.0
        return r - self.div_yield - b

    @classmethod
    def flat(cls, today: date, spot: float, vol: float, r: float, q: float,
             market: str = "US", curve_ccy: str = "USD",
             borrow_spread_bps: float = 0.0) -> "EquitySnapshot":
        """Convenience constructor for a flat-rate snapshot (no term structure)."""
        return cls(today=today, spot=spot, vol=vol, div_yield=q,
                   discount_curve=None, curve_ccy=curve_ccy, market=market, flat_r=r,
                   borrow_spread_bps=borrow_spread_bps)

    def with_drift_override(self, drift: float) -> "EquitySnapshot":
        """Return a shallow copy with drift_override set.

        Used by multi-scenario analysis: same market state, different macro drift
        assumptions (bullish / flat / bearish).
        """
        import copy
        new = copy.copy(self)
        new.drift_override = float(drift)
        return new

    def with_vol_surface(self, vol_surface: Any) -> "EquitySnapshot":
        """Return a shallow copy carrying a smile/local-vol surface."""
        import copy
        new = copy.copy(self)
        new.vol_surface = vol_surface
        return new

    def path_vol(self, t: float, spot, *, parallel_bump: float = 0.0):
        """Return path-generation volatility at time/state.

        Flat-vol mode returns mkt.vol. Surface mode delegates to the loaded
        volatility surface and applies a parallel vol bump for vega.
        """
        if self.vol_surface is not None:
            return self.vol_surface.local_vol(t, spot, parallel_bump=parallel_bump)
        return max(1e-6, float(self.vol) + float(parallel_bump))


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
    borrow_spread_bps: float = 0.0,
) -> EquitySnapshot:
    """Build an EquitySnapshot by auto-detecting market and loading the appropriate curve.

    Steps:
      1. Infer exchange (US / HK) from ticker or exchange_hint.
      2. Fetch spot price and dividend yield (via prices.py / yfinance) if needed.
      3. Load discount curve: US -> SOFR; HK -> HIBOR (TMA).
      4. Return a ready-to-use EquitySnapshot for the MC engine.
    """
    mkt = infer_market(ticker, exchange_hint)
    px = None
    if spot_override is None or override_div_yield is None:
        px = get_spot_div_snapshot(ticker, today)
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
        flat_r=flat_r,
        borrow_spread_bps=float(borrow_spread_bps),
    )
