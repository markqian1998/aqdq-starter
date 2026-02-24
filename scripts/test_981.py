"""Price a 981.HK (CNOOC) Accumulator using the exact 245-day schedule from the term sheet."""

from datetime import date
from pathlib import Path
import QuantLib as ql

from src.models.terms import AQDQTerms, AQDQSchedule, resolve_exchange_calendar
from src.models.schedule_loaders import read_ts_periods_csv, build_observation_schedule_from_ts
from src.pricer import price_with_greeks

# ---- 1. Contract terms (from term sheet) ----
terms = AQDQTerms(
    product_type="AQ", side="buy", currency="HKD",
    forward_price=46.6043, ko_level=59.1090,
    shares_per_day=855, max_obs_days=245, gear_ratio=2,
    max_total_shares=418950, gtd_days=37,
    ticker="0981.HK", exchange_hint="HK",
)

terms.aq_mode = "speedy"    # switch to "regular" to compare settlement conventions
settlement_mode = "daily"   # "final" (lump at maturity) or "daily" (mark-to-market)

# ---- 2. Load the exact 245-day observation schedule from the TS CSV ----
ROOT    = Path(__file__).resolve().parents[1]
CSV     = ROOT / "data" / "981HK_periods.csv"

print("Using CSV:", CSV)
periods = read_ts_periods_csv(str(CSV))
cal     = resolve_exchange_calendar(terms.ticker or "", terms.exchange_hint)
obs     = build_observation_schedule_from_ts(cal, periods)

sched = AQDQSchedule(
    effective_date=periods[0]["start"],
    final_accum_date=periods[-1]["end"],
    calendar=cal,
    explicit_schedule=obs,   # pin to the 245 term-sheet days
)

# ---- 3. Market inputs (replace with live data for production use) ----
today = date(2025, 8, 20)
spot  = 51.70
r, q  = 0.03, 0.01
vol   = 0.47424

# ---- 4. Price ----
res = price_with_greeks(terms, sched, today, spot, r, q, vol,
                        delivered_to_date=0.0, n_paths=20000,
                        settlement_mode=settlement_mode)

print("PV     :", res["pv"])
print("StdErr :", res["std_err"])
print("Greeks :", res["greeks"])
print("Meta   :", res["meta"])
