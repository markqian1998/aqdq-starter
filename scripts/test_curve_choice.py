# scripts/test_curve_choice.py
"""Demonstrate automatic SOFR (US) and HIBOR (HK) curve selection.

Prices two example products — one HK equity and one US equity — and shows
how build_snapshot_auto() picks the correct discount curve automatically.
"""

from datetime import date, timedelta
import QuantLib as ql

from src.models.terms import AQDQTerms, AQDQSchedule, resolve_exchange_calendar
from src.pricer import price_with_greeks


# ====== Helper: build a schedule from N trading days ======

def make_sched_by_obs_days(
    effective_date: date, max_obs_days: int, calendar: ql.Calendar
) -> AQDQSchedule:
    """Count forward max_obs_days exchange business days from effective_date.

    Returns an AQDQSchedule whose final_accum_date is the last of those days.
    """
    d   = effective_date
    obs = []
    while len(obs) < max_obs_days:
        qld = ql.Date(d.day, d.month, d.year)
        if calendar.isBusinessDay(qld):
            obs.append(d)
        d += timedelta(days=1)
    final_accum = obs[-1]
    return AQDQSchedule(
        effective_date=effective_date,
        final_accum_date=final_accum,
        calendar=calendar,
        # Uncomment to pin the exact observation dates:
        # explicit_schedule=obs,
    )


# ====== Common valuation date ======
today = date(2025, 8, 26)


# ====== 1) HK example: 981.HK — HIBOR discount curve ======

terms_hk = AQDQTerms(
    product_type="AQ", side="buy", currency="HKD",
    forward_price=46.6043, ko_level=59.1090,
    shares_per_day=855, max_obs_days=245, gear_ratio=2,
    max_total_shares=418950, gtd_days=37,
    ticker="0981.HK", exchange_hint="HK",
)

cal_hk   = resolve_exchange_calendar(terms_hk.ticker or "", terms_hk.exchange_hint)
sched_hk = make_sched_by_obs_days(date(2025, 2, 26), terms_hk.max_obs_days, cal_hk)

# spot/r/q=None: build_snapshot_auto() fetches live data and loads HIBOR curve automatically
res_hk = price_with_greeks(
    terms_hk, sched_hk, today,
    spot=None, r=None, q=None,   # auto-fetch spot & HIBOR curve for HK
    vol=0.45,
    n_paths=10000,
    settlement_mode="final",     # or "daily"
)
print("[HK] PV     :", res_hk["pv"])
print("[HK] StdErr :", res_hk["std_err"])
print("[HK] Greeks :", res_hk["greeks"])
print("[HK] Meta   :", res_hk["meta"])


# ====== 2) US example: AAPL — SOFR discount curve ======

terms_us = AQDQTerms(
    product_type="AQ", side="buy", currency="USD",
    forward_price=225.0, ko_level=260.0,
    shares_per_day=100, max_obs_days=245, gear_ratio=2,
    max_total_shares=24500, gtd_days=37,
    ticker="AAPL US", exchange_hint="US",
)

cal_us   = resolve_exchange_calendar(terms_us.ticker or "", terms_us.exchange_hint)
sched_us = make_sched_by_obs_days(date(2025, 2, 26), terms_us.max_obs_days, cal_us)

# spot/r/q=None: build_snapshot_auto() fetches live data and loads SOFR curve automatically
res_us = price_with_greeks(
    terms_us, sched_us, today,
    spot=None, r=None, q=None,   # auto-fetch spot & SOFR curve for US
    vol=0.35,
    n_paths=10000,
    settlement_mode="final",
)
print("[US] PV     :", res_us["pv"])
print("[US] StdErr :", res_us["std_err"])
print("[US] Greeks :", res_us["greeks"])
print("[US] Meta   :", res_us["meta"])
