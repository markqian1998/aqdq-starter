from datetime import date
from src.models.terms import AQDQTerms, AQDQSchedule

# ---- Step 1: Define contract terms (HK Accumulator example) ----
terms_HK = AQDQTerms(
    product_type="AQ",           # Accumulator (buy-side)
    side="buy",
    currency="HKD",
    forward_price=46.6043,
    ko_level=59.1090,
    shares_per_day=855,
    max_obs_days=245,
    gear_ratio=2,
    max_total_shares=418950,
    gtd_days=37,
    ticker="0981.HK",
    exchange_hint="HK"
)

# ---- Step 2: Define the observation schedule ----
sched_HK = AQDQSchedule(
    effective_date=date(2025, 2, 27),
    final_accum_date=date(2026, 2, 25)
)

# ---- Step 3: Run basic sanity checks ----
print("HK observation day count:", len(sched_HK.observation_dates(terms_HK)))
print("HK GTD total days (from terms):", sched_HK.gtd_days_total(terms_HK))
print("HK GTD window (first 5):", sched_HK.gtd_window_dates(terms_HK)[:5], "...")
print("HK remaining obs as of 2025-08-20:",
      len(sched_HK.remaining_observation_dates(terms_HK, date(2025, 8, 20))))


# ---- US Accumulator example ----
terms_US = AQDQTerms(
    product_type="AQ",
    side="buy",
    currency="USD",
    forward_price=247.8898,
    ko_level=313.1303,
    shares_per_day=32,
    max_obs_days=250,
    gear_ratio=2,
    max_total_shares=16000,
    gtd_days=39,
    ticker="MSTR US",
    exchange_hint="US"
)

sched_US = AQDQSchedule(
    effective_date=date(2025, 2, 25),
    final_accum_date=date(2026, 2, 23)
)

print("\nUS observation day count:", len(sched_US.observation_dates(terms_US)))
print("US GTD total days (from terms):", sched_US.gtd_days_total(terms_US))
print("US GTD window (first 5):", sched_US.gtd_window_dates(terms_US)[:5], "...")
print("US remaining obs as of 2025-08-20:",
      len(sched_US.remaining_observation_dates(terms_US, date(2025, 8, 20))))
