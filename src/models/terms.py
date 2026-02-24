# src/models/terms.py
"""Data classes and helper functions for AQ/DQ contract terms and observation schedules."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, Optional, List
import re
import QuantLib as ql

Side   = Literal["buy", "sell"]    # AQ = buy, DQ = sell
Prod   = Literal["AQ", "DQ"]       # product type
Dir    = Literal["above", "below"] # direction for KO / LNBD triggers
AQMode = Literal["regular", "speedy"]


# ---- Exchange calendar resolver ----

def resolve_exchange_calendar(ticker: str, exchange_hint: Optional[str] = None) -> ql.Calendar:
    """Return the appropriate QuantLib Calendar for the given ticker or exchange.

    Covers major markets: HK, US, UK, JP, SG, KR, TW, CN, DE, FR, CH, CA, AU.
    Falls back to TARGET calendar if the exchange cannot be determined.

    Priority:
      1. exchange_hint (explicit override)
      2. Ticker suffix pattern (e.g. ".HK", " HK", " JP")
    """
    t  = ticker.upper().strip()
    ex = (exchange_hint or "").upper().strip()

    def cal_UK():
        return ql.UnitedKingdom(ql.UnitedKingdom.Exchange)

    # --- Explicit exchange hint takes priority ---
    if ex in {"US", "NYSE", "NASDAQ"}:
        return ql.UnitedStates(ql.UnitedStates.NYSE)

    if ex in {"UK", "LSE", "LN"}:
        return cal_UK()

    if ex in {"HK", "HKEX"}:
        cal_HK = ql.HongKong()
        # Supplement QuantLib's built-in HK calendar with explicit holidays 2025-2030
        holidays_HK = [
            # 2025
            ql.Date(1,  1,  2025),  # New Year's Day
            ql.Date(4,  4,  2025),  # Ching Ming Festival
            ql.Date(18, 4,  2025),  # Good Friday
            ql.Date(21, 4,  2025),  # Easter Monday
            ql.Date(1,  5,  2025),  # Labour Day
            ql.Date(5,  5,  2025),  # Buddha's Birthday
            ql.Date(1,  7,  2025),  # HKSAR Establishment Day
            ql.Date(1,  10, 2025),  # National Day
            ql.Date(7,  10, 2025),  # Day after Mid-Autumn Festival
            ql.Date(29, 10, 2025),  # Chung Yeung Festival
            ql.Date(25, 12, 2025),  # Christmas Day
            ql.Date(26, 12, 2025),  # Boxing Day
            ql.Date(17, 2,  2025),  # Lunar New Year (carried from 2026 schedule)
            # 2026
            ql.Date(1,  1,  2026),  # New Year's Day
            ql.Date(17, 2,  2026),  # Lunar New Year Day 1
            ql.Date(18, 2,  2026),  # Lunar New Year Day 2
            ql.Date(19, 2,  2026),  # Lunar New Year Day 3
            ql.Date(5,  4,  2026),  # Ching Ming Festival
            ql.Date(3,  4,  2026),  # Good Friday
            ql.Date(6,  4,  2026),  # Easter Monday
            ql.Date(1,  5,  2026),  # Labour Day
            ql.Date(12, 5,  2026),  # Buddha's Birthday
            ql.Date(1,  7,  2026),  # HKSAR Establishment Day
            ql.Date(1,  10, 2026),  # National Day
            ql.Date(3,  10, 2026),  # Day after Mid-Autumn Festival
            ql.Date(21, 10, 2026),  # Chung Yeung Festival
            ql.Date(25, 12, 2026),  # Christmas Day
            # 2027
            ql.Date(1,  1,  2027),  # New Year's Day
            ql.Date(29, 1,  2027),  # Lunar New Year Day 1
            ql.Date(30, 1,  2027),  # Lunar New Year Day 2
            ql.Date(31, 1,  2027),  # Lunar New Year Day 3
            ql.Date(4,  4,  2027),  # Ching Ming Festival
            ql.Date(26, 3,  2027),  # Good Friday
            ql.Date(29, 3,  2027),  # Easter Monday
            ql.Date(1,  5,  2027),  # Labour Day
            ql.Date(4,  5,  2027),  # Buddha's Birthday
            ql.Date(1,  7,  2027),  # HKSAR Establishment Day
            ql.Date(1,  10, 2027),  # National Day
            ql.Date(20, 9,  2027),  # Day after Mid-Autumn Festival
            ql.Date(21, 10, 2027),  # Chung Yeung Festival
            ql.Date(25, 12, 2027),  # Christmas Day
            # 2028
            ql.Date(1,  1,  2028),  # New Year's Day
            ql.Date(13, 2,  2028),  # Lunar New Year Day 1
            ql.Date(14, 2,  2028),  # Lunar New Year Day 2
            ql.Date(15, 2,  2028),  # Lunar New Year Day 3
            ql.Date(4,  4,  2028),  # Ching Ming Festival
            ql.Date(14, 4,  2028),  # Good Friday
            ql.Date(17, 4,  2028),  # Easter Monday
            ql.Date(1,  5,  2028),  # Labour Day
            ql.Date(22, 5,  2028),  # Buddha's Birthday
            ql.Date(1,  7,  2028),  # HKSAR Establishment Day
            ql.Date(1,  10, 2028),  # National Day
            ql.Date(8,  10, 2028),  # Day after Mid-Autumn Festival
            ql.Date(17, 10, 2028),  # Chung Yeung Festival
            ql.Date(25, 12, 2028),  # Christmas Day
            # 2029
            ql.Date(1,  1,  2029),  # New Year's Day
            ql.Date(1,  2,  2029),  # Lunar New Year Day 1
            ql.Date(2,  2,  2029),  # Lunar New Year Day 2
            ql.Date(3,  2,  2029),  # Lunar New Year Day 3
            ql.Date(5,  4,  2029),  # Ching Ming Festival
            ql.Date(30, 3,  2029),  # Good Friday
            ql.Date(2,  4,  2029),  # Easter Monday
            ql.Date(1,  5,  2029),  # Labour Day
            ql.Date(9,  5,  2029),  # Buddha's Birthday
            ql.Date(1,  7,  2029),  # HKSAR Establishment Day
            ql.Date(1,  10, 2029),  # National Day
            ql.Date(27, 9,  2029),  # Day after Mid-Autumn Festival
            ql.Date(5,  10, 2029),  # Chung Yeung Festival
            ql.Date(25, 12, 2029),  # Christmas Day
            # 2030
            ql.Date(1,  1,  2030),  # New Year's Day
            ql.Date(22, 1,  2030),  # Lunar New Year Day 1
            ql.Date(23, 1,  2030),  # Lunar New Year Day 2
            ql.Date(24, 1,  2030),  # Lunar New Year Day 3
            ql.Date(5,  4,  2030),  # Ching Ming Festival
            ql.Date(19, 4,  2030),  # Good Friday
            ql.Date(22, 4,  2030),  # Easter Monday
            ql.Date(1,  5,  2030),  # Labour Day
            ql.Date(28, 5,  2030),  # Buddha's Birthday
            ql.Date(1,  7,  2030),  # HKSAR Establishment Day
            ql.Date(1,  10, 2030),  # National Day
            ql.Date(16, 9,  2030),  # Day after Mid-Autumn Festival
            ql.Date(24, 10, 2030),  # Chung Yeung Festival
            ql.Date(25, 12, 2030),  # Christmas Day
        ]
        for h in holidays_HK:
            cal_HK.addHoliday(h)
        return cal_HK

    if ex in {"JP", "TSE"}:            return ql.Japan()
    if ex in {"SG", "SGX"}:            return ql.Singapore()
    if ex in {"KR", "KS", "KSE"}:     return ql.SouthKorea()
    if ex in {"TW", "TWSE"}:          return ql.Taiwan()
    if ex in {"CH", "CN", "SSE", "SH"}: return ql.China(ql.China.SSE)
    if ex in {"DE", "XETRA", "GR", "GY"}: return ql.Germany(ql.Germany.Xetra)
    if ex in {"FR", "PAR", "FP"}:     return ql.France()
    if ex in {"CHS", "SW", "SIX"}:    return ql.Switzerland()
    if ex in {"CA", "TSX"}:           return ql.Canada()
    if ex in {"AU", "ASX"}:           return ql.Australia()

    # --- Parse common ticker formats ---
    # Examples: "981 HK Equity", "0981.HK", "AAPL US Equity", "7203 JP", "600519 CH Equity"
    m   = re.search(r'[.\s]([A-Z]{2})\b', t)   # capture two-letter exchange suffix
    suf = m.group(1) if m else ""

    if suf == "HK": return ql.HongKong()
    if suf == "US": return ql.UnitedStates(ql.UnitedStates.NYSE)
    if suf == "JP": return ql.Japan()
    if suf in {"SG", "SI"}:           return ql.Singapore()
    if suf in {"KS", "KR"}:           return ql.SouthKorea()
    if suf in {"TT", "TW"}:           return ql.Taiwan()
    if suf in {"CH", "SS", "SH", "SZ"}: return ql.China(ql.China.SSE)
    if suf in {"LN", "GB"}:           return cal_UK()
    if suf in {"GR", "GY", "DE"}:     return ql.Germany(ql.Germany.Xetra)
    if suf in {"FP", "FR"}:           return ql.France()
    if suf in {"SW"}:                 return ql.Switzerland()
    if suf in {"AU", "AX"}:           return ql.Australia()
    if suf in {"CA", "TO"}:           return ql.Canada()

    # Final fallback
    return ql.TARGET()


def resolve_daycounter(currency: str) -> ql.DayCounter:
    """Return the day-count convention for the given currency.

    Equity derivatives commonly use Actual/365Fixed for all major currencies.
    Extend here if currency-specific conventions are needed.
    """
    return ql.Actual365Fixed()


# ---- Contract terms ----

@dataclass
class AQDQTerms:
    """Structured representation of an AQ/DQ product's key priceable parameters."""
    product_type: Prod
    side: Side
    currency: str
    forward_price: float         # accumulation / delivery price (K)
    ko_level: float              # knock-out barrier
    shares_per_day: float        # shares accumulated per observation day (base, without gear)
    max_obs_days: int            # maximum number of observation days in the contract
    gtd_days: int                # guaranteed trading days (GTD window length)
    aq_mode: AQMode = "regular"  # "regular" or "speedy"
    gear_ratio: int = 2          # LNBD multiplier (typically 2x)
    max_total_shares: Optional[float] = None  # total share cap; None = unlimited
    enable_pnbd: bool = True     # enable PNBD lump-sum on KO within GTD
    ko_direction: Dir = "above"  # KO triggers when spot goes above/below the barrier
    lnbd_direction: Dir = "below"  # LNBD gear applies when spot is below/above forward price
    # Multi-market fields
    ticker: Optional[str] = None          # e.g. "AAPL US Equity", "0981.HK", "7203 JP"
    exchange_hint: Optional[str] = None   # e.g. "US", "HK", "JP" (overrides auto-detection)
    settlement_loc_hint: Optional[str] = None  # reserved for T+2 settlement calendar


# ---- Observation schedule ----

@dataclass
class AQDQSchedule:
    """Defines the observation date schedule for an AQ/DQ product."""
    effective_date: date        # first observation date (natural calendar)
    final_accum_date: date      # last accumulation date (natural calendar)
    calendar: Optional[ql.Calendar] = None    # exchange calendar (auto-resolved if None)
    dc: Optional[ql.DayCounter] = None        # day-count convention (auto-resolved if None)
    explicit_schedule: Optional[List[date]] = None  # if provided, overrides calendar generation

    def bind_market_conventions(self, terms: AQDQTerms):
        """Resolve calendar and day-counter from the contract terms if not already set."""
        if self.calendar is None:
            self.calendar = resolve_exchange_calendar(terms.ticker or "", terms.exchange_hint)
        if self.dc is None:
            self.dc = resolve_daycounter(terms.currency)

    def observation_dates(self, terms: AQDQTerms) -> List[date]:
        """Return the full list of observation dates for this schedule.

        If explicit_schedule is provided, it is used directly (sorted and deduplicated).
        Otherwise, all exchange business days between effective_date and final_accum_date
        are enumerated using the resolved calendar.
        """
        if self.explicit_schedule is not None:
            return sorted(list(dict.fromkeys(self.explicit_schedule)))
        self.bind_market_conventions(terms)
        d, out = self.effective_date, []
        while d <= self.final_accum_date:
            qld = ql.Date(d.day, d.month, d.year)
            if self.calendar.isBusinessDay(qld):
                out.append(d)
            d += timedelta(days=1)
        return out

    def gtd_days_total(self, terms: AQDQTerms) -> int:
        """Return the total number of GTD days as specified in the contract terms."""
        return int(terms.gtd_days)

    def gtd_window_dates(self, terms: AQDQTerms) -> List[date]:
        """Return the observation dates that fall within the GTD window.

        The GTD window is the first gtd_days observation dates.
        Used to determine whether a KO event occurred during the protected period.
        """
        obs = self.observation_dates(terms)
        return obs[: int(terms.gtd_days)]

    def gtd_days_remaining(self, terms: AQDQTerms, today: date, include_today_close: bool = False) -> int:
        """Return the number of GTD observation dates remaining as of today.

        Used to compute the PNBD lump-sum share count when KO occurs within the GTD window.
        """
        window = self.gtd_window_dates(terms)
        if include_today_close:
            return sum(1 for d in window if d >= today)
        else:
            return sum(1 for d in window if d > today)

    def remaining_observation_dates(self, terms: AQDQTerms, today: date, include_today_close: bool = False) -> List[date]:
        """Return the observation dates remaining after (or from) today.

        This is the discrete time axis used by the MC engine for path simulation,
        KO checking, daily accumulation, and daily discounting.
        """
        obs = self.observation_dates(terms)
        if include_today_close:
            return [d for d in obs if d >= today]
        else:
            return [d for d in obs if d > today]
