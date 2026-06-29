# How to Price a New Ticker — Operations Manual

This is the step-by-step guide for running the accumulator model on a new
underlying. No coding required — you copy a template, edit a few fields, and
run one command.

> If you want the engine internals (the math, the modules), see the main
> [README](../README.md). This document is only about **operating** the tool.

---

## TL;DR

```bash
# 1. Copy the template
cp trades/TEMPLATE.yaml trades/my_ticker.yaml

# 2. Edit the 7 fields marked 【改】 in my_ticker.yaml

# 3. Run
python3 aqdq_cli.py price trades/my_ticker.yaml
```

Reports land in `reports/`: one Markdown summary, one JSON, and the plots.

---

## One-time setup

You only do this once per machine.

```bash
cd ~/aqdq_starter
pip install -r requirements.txt
```

Verify it works by pricing the bundled demo:

```bash
python3 aqdq_cli.py price trades/AVGO_AQ_20260512.yaml --paths 50000 --no-plot
```

You should see a four-line scenario summary printed to the screen. If you do,
you're ready.

---

## Step 1 — Copy the template

Every trade is one YAML file in the `trades/` folder. Start from the template:

```bash
cp trades/TEMPLATE.yaml trades/my_ticker.yaml
```

Name the file something recognisable, e.g. `googl_aq_20260522.yaml`.

---

## Step 2 — Edit the fields

Open your new file. Only the fields marked **【改】** must be changed; everything
else has a sensible default. The seven essentials:

| Field | What it is | Example |
|-------|-----------|---------|
| `trade_id` | Name of the trade (used in report filenames, no spaces) | `GOOGL_AQ_001` |
| `ticker` | Stock code. US: plain code. HK: add `.HK` | `GOOGL` / `0700.HK` |
| `spot` | Current share price (set `null` to auto-fetch from Yahoo) | `175.00` |
| `strike_pct_of_spot` | Strike as a fraction of spot | `0.85` (= buy at 15% discount) |
| `ko_pct_of_spot` | Knock-out barrier as a fraction of spot | `1.05` (= terminate if +5%) |
| `flat_vol` | Annualised volatility (see Step 3 for the precise route) | `0.32` |
| `pricing_date` | Valuation date, usually today | `2026-05-22` |

The remaining contract terms — `tenor_months`, `observation`, `shares_per_period`,
`gear_ratio`, `guarantee_periods`, `cap_shares` — should be set from the actual
term sheet. The template comments explain each one.

### Absolute vs percentage strike — important

There are two ways to set strike and KO:

- **New trade (percentage):** use `strike_pct_of_spot` / `ko_pct_of_spot`. The
  engine multiplies them by spot to get the absolute levels.
- **Existing trade you're re-pricing (absolute):** use `strike:` and `ko_level:`
  with the locked-in dollar values. **Do this for any mark-to-market.** If you
  leave it as a percentage and the spot has moved, you would silently re-price a
  *different* contract — the strike would drift with today's spot instead of
  staying where it was struck.

```yaml
terms:
  # New trade — let the engine resolve from spot:
  strike_pct_of_spot: 0.85
  ko_pct_of_spot: 1.05

  # OR, existing trade MTM — hard-code the struck levels:
  # strike: 178.13
  # ko_level: 375.23
```

---

## Step 3 — Choose your volatility input

This is the one real decision. There are two modes.

### Mode A — Flat vol (quick look)

Put a single number in `flat_vol` and set `vol_input: flat`. Two minutes to a
result. Good for an initial screen of whether a trade is worth analysing.

```yaml
market:
  flat_vol: 0.32
  vol_input: flat
```

### Mode B — Vol surface (accurate / client-facing)

Accumulators carry a knock-out barrier, and **KO probability depends heavily on
the volatility skew**. A flat vol systematically misprices KO probability — it
can be off by 5–10 percentage points. So for anything that touches a real
position or a client, use a surface.

The surface comes from a Bloomberg `OVDV` or Derivitec screenshot, exported to a
CSV with these exact columns:

```
Expiry, ATM Fwd, ATM Vol, Put Vol, Call Vol, Skew, Smile, Call Wing, Put Wing
```

See `data/vol_surfaces/BE_Vol_Surface_SLV_20260522.csv` for a working example.

**Where to put the CSV and how to name it.** All vol surfaces live in
`data/vol_surfaces/`, named `TICKER_Vol_Surface_SLV_YYYYMMDD.csv` where the date
is the **as-of date the surface was captured**. Volatility moves every day, so
each pull is a new dated file — never overwrite yesterday's; keep them for
history and reproducibility.

Then in the YAML:

```yaml
market:
  vol_input: slv
  vol_surface:
    model: slv_param_curve
    path: data/vol_surfaces/GOOGL_Vol_Surface_SLV_20260522.csv
    put_moneyness: 0.80
    call_moneyness: 1.20
```

When `vol_input: slv`, the `flat_vol` value is ignored.

> **Current limitation:** exporting the surface CSV from Bloomberg is still a
> manual step. Automating screenshot/PDF → surface is planned but not built yet.

---

## Step 4 — Run

```bash
python3 aqdq_cli.py price trades/my_ticker.yaml
```

Useful flags:

| Flag | Effect |
|------|--------|
| `--paths 50000` | Fewer paths = faster, noisier. Use 50k for a quick look, the default 200k for a report. |
| `--no-plot` | Skip the charts (faster). |
| `--spot 1320` | Override the pricing spot for a what-if. **Does not change the strike/KO** — those stay as the contract defines them. |
| `--scenarios risk_neutral,bearish` | Run only some of the scenarios listed in the file. |
| `--output-dir somewhere/` | Write reports somewhere other than `reports/`. |

---

## Step 5 — Read the output

Each run creates one self-contained folder, `reports/TICKER_AQ_YYYYMMDD/`,
holding everything for that run:

- **`report.md`** — the human-readable risk note (open in any Markdown viewer
  or VS Code preview). Plot images are referenced by relative path, so you can
  zip and share the whole folder and the charts still render.
- **`report.json`** — every number, for downstream tools.
- **`plots/<scenario>/`** — four charts per scenario: price paths, KO timing,
  share-accumulation distribution, and the tail-risk bar chart.

The folder name is derived automatically from the trade's ticker, product type,
and pricing date — so reports stay consistently named no matter what you typed
in `trade_id`.

What the headline numbers mean:

| Metric | Plain English |
|--------|---------------|
| **KO probability** | Chance the trade terminates early (spot hits the KO barrier). |
| **Expected / median shares** | How many shares the client ends up accumulating, on average / typically. |
| **p95 shares** | A bad-but-plausible case (95th percentile) — used for sizing cash buffers. |
| **Tail-risk table** | Probability of accumulating ≥ X% of the cap, plus the cash that requires at strike. **This is the key buy-side risk view.** |
| **PV (risk-neutral only)** | Model fair value of the structure. Negative = the discount the client received is outweighed by the asymmetric risk they took on. |

> **Read PV only from the `risk_neutral` scenario.** The bull/bear/flat scenarios
> are real-world stress illustrations — their "PV" is not a fair value and must
> not be quoted as one.

---

## Two common workflows

### A. Screen a new trade idea

```bash
cp trades/TEMPLATE.yaml trades/GOOGL_AQ_20260522.yaml   # TICKER_AQ_YYYYMMDD
# edit ticker, spot, strike%, ko%, flat_vol
python3 aqdq_cli.py price trades/GOOGL_AQ_20260522.yaml --paths 50000 --no-plot
```

### B. Mark-to-market an existing position

1. Copy the original trade's YAML.
2. Change `strike_pct_of_spot`/`ko_pct_of_spot` to **absolute** `strike`/`ko_level`
   (the struck values). This is the key step — see the warning under Step 2.
3. Update `pricing_date` and `spot` to today.
4. Refresh the vol surface CSV if the spot has moved materially (pull a new
   `data/vol_surfaces/TICKER_Vol_Surface_SLV_<today>.csv` and point the YAML at it).
5. Run.

---

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `Trade ...: must provide either terms.strike or both strike_pct_of_spot AND market.spot` | You used a percentage strike but left `spot: null`. Either set a spot or use an absolute strike. |
| Hangs / network error on run | It's trying to auto-fetch spot or a curve from the internet. Set `spot`, `div_yield`, and `flat_rate` explicitly to run fully offline. |
| KO probability looks too high | Check `observation:` — a daily contract has far more chances to knock out than a weekly/bi-weekly one. Make sure it matches the term sheet. |
| Report filename collides / overwrites | Reports are named `<trade_id>_<pricing_date>`. Running the same trade twice on the same day overwrites. Change `trade_id` or `pricing_date` to keep both. |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt`, and run commands from the `~/aqdq_starter` root. |

---

## Optional: exact term-sheet schedule

By default the engine generates the observation schedule from the `observation`
frequency. If a term sheet specifies exact observation periods, supply them as a
CSV (`period, start, end, days` — see `data/unh_periods.csv`) and reference it:

```yaml
schedule:
  source: term_sheet_periods_csv
  path: data/my_periods.csv
```

This block sits at the top level of the YAML (same indentation as `market:`),
not inside `market:`.
