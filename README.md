# aqdq-starter

A Monte Carlo pricing engine for **Accumulator / Decumulator (AQ/DQ)** equity structured products, with support for both Hong Kong (HIBOR) and US (SOFR) markets.

---

## Features

- **Monte Carlo pricing** with Geometric Brownian Motion (GBM) and antithetic variance reduction
- **Full product mechanics**: daily share accumulation, Knock-Out (KO), LNBD gear multiplier, GTD guaranteed-accumulation window, PNBD lump-sum on early termination
- **Two settlement modes**: `final` (sell all shares at maturity) and `daily` (mark-to-market each observation day)
- **Two AQ modes**: `regular` (daily accumulation throughout GTD) and `speedy` (lump-sum GTD grant on Day 2)
- **Greeks**: Delta, Gamma, Vega via finite-difference bump-and-reprice using common random numbers
- **Multi-market**: automatic SOFR (USD) or HIBOR (HKD) discount curve selection based on ticker / exchange hint
- **Exchange calendars**: 20+ markets supported via QuantLib (HK, US, JP, SG, KR, TW, CN, DE, FR, UK, CH, CA, AU, …)

---

## Installation

```bash
pip install -r requirements.txt
```

> **QuantLib note**: `QuantLib-Python` requires a C++ build; if the pip wheel fails, see [QuantLib installation guide](https://www.quantlib.org/).

---

## 📕 New here? Want to price a ticker without touching the code?

See **[docs/HOW_TO_RUN.md](docs/HOW_TO_RUN.md)** — a step-by-step operations
manual: copy a template YAML, edit a few fields, run one command. No Python
required.

```bash
cp trades/TEMPLATE.yaml trades/my_ticker.yaml   # edit the 7 fields marked 【改】
python3 aqdq_cli.py price trades/my_ticker.yaml
```

The rest of this README is the developer / API reference.

---

## Quick Start

### Price a Hong Kong Accumulator (981.HK)

```python
from datetime import date
from src.models.terms import AQDQTerms, AQDQSchedule
from src.pricer import price_with_greeks

# 1. Contract terms (from the term sheet)
terms = AQDQTerms(
    product_type="AQ", side="buy", currency="HKD",
    forward_price=46.6043, ko_level=59.1090,
    shares_per_day=855, max_obs_days=245, gear_ratio=2,
    max_total_shares=418950, gtd_days=37,
    ticker="0981.HK", exchange_hint="HK",
    aq_mode="speedy",
)

# 2. Observation schedule (calendar-generated)
sched = AQDQSchedule(
    effective_date=date(2025, 2, 27),
    final_accum_date=date(2026, 2, 25),
)

# 3. Price
result = price_with_greeks(
    terms=terms, schedule=sched,
    today=date(2025, 8, 20),
    spot=51.70, r=0.03, q=0.01, vol=0.474,
    n_paths=20000, settlement_mode="daily",
)
print("PV    :", result["pv"])
print("StdErr:", result["std_err"])
print("Greeks:", result["greeks"])
print("Meta  :", result["meta"])
```

### Price from a Bloomberg CSV schedule

```python
from pathlib import Path
from src.models.schedule_loaders import read_ts_periods_csv, build_observation_schedule_from_ts
from src.models.terms import resolve_exchange_calendar

ROOT = Path(__file__).resolve().parent
csv  = ROOT / "data" / "981HK_periods.csv"

periods = read_ts_periods_csv(str(csv))
cal     = resolve_exchange_calendar(terms.ticker, terms.exchange_hint)
obs     = build_observation_schedule_from_ts(cal, periods)

sched = AQDQSchedule(
    effective_date=periods[0]["start"],
    final_accum_date=periods[-1]["end"],
    calendar=cal,
    explicit_schedule=obs,   # pin to the exact 245 TS days
)
```

---

## Project Structure

```
aqdq_starter/
├── README.md
├── LICENSE
├── requirements.txt
├── .gitignore
├── data/
│   └── 981HK_periods.csv          # Example observation-period CSV (245 HK trading days)
├── scripts/
│   ├── test_981.py                # Full pricing example: 981.HK (CNOOC)
│   ├── test_curve_choice.py       # Demo: automatic SOFR vs HIBOR selection
│   └── test_hibor_curve.py        # HIBOR curve diagnostics
└── src/
    ├── pricer.py                  # Top-level API: price_with_greeks()
    ├── models/
    │   ├── terms.py               # AQDQTerms, AQDQSchedule, exchange calendar resolver
    │   └── schedule_loaders.py    # CSV period loader
    ├── market/
    │   ├── snapshot.py            # EquitySnapshot, build_snapshot_auto()
    │   └── feeds/
    │       ├── prices.py          # Spot & dividend fetch (yfinance)
    │       ├── hibor_tma.py       # HK HIBOR curve from TMA
    │       └── sofr_curve.py      # USD SOFR OIS bootstrap
    └── engines/
        ├── mc.py                  # Monte Carlo pricing engine + Greeks
        ├── paths.py               # GBM antithetic path generation
        └── payoff.py              # Path-wise share accumulation & KO logic
```

---

## Market Data

### HIBOR (HKD)
Fetched live from the [TMA benchmark page](https://benchmark.tma.org.hk/) at pricing time.
No API key required.

### SOFR (USD)
`get_default_usd_sofr_curve()` bootstraps a USD SOFR OIS discount curve from a set of
representative par swap rates included in `sofr_curve.py`.

**For production use**, replace `DEFAULT_SOFR_PILLARS` with live Bloomberg data:

```python
from src.market.feeds.sofr_curve import read_bbg_horizon_excel, build_sofr_discount_from_swaps

pillars = read_bbg_horizon_excel("path/to/sofr_ois_export.xlsx")
curve   = build_sofr_discount_from_swaps(today=today, swap_pillars=pillars)
```

---

## Running the Tests

```bash
# Snapshot / market data
python src/market/test_snapshot.py

# Payoff engine
python src/engines/test_payoff.py

# GBM path generation (opens a matplotlib chart)
python src/engines/test_paths.py

# Observation schedule
python src/models/test_schedule.py

# Full pricing example (requires network for HIBOR)
python scripts/test_981.py
```

---

## License

MIT — see [LICENSE](LICENSE).
Copyright © 2025 Mark Qian.
