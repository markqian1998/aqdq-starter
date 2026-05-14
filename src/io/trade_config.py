# src/io/trade_config.py
"""YAML / JSON trade configuration loader.

Translates a human-friendly trade spec like:

    trade_id: AVGO_DEMO_001
    underlying:
      ticker: AVGO
      exchange: US
    terms:
      strike_pct_of_spot: 0.85
      ko_pct_of_spot: 1.05
      tenor_months: 12
      ...

into the dataclasses the pricing engine consumes:
    AQDQTerms, AQDQSchedule, EquitySnapshot, MCSettings, ScenarioSpec list.

Why YAML over Python scripts (the old `scripts/test_981.py` pattern):
  - One source of truth per trade, diffable in git, reviewable line-by-line.
  - Same file feeds the pricing engine today, the web app tomorrow, the
    Airtable sync the day after. No code duplication.
  - LLM-friendly: an analyst can ask an AI to populate the YAML from a
    termsheet PDF without writing Python.

DEALER NOTE: Sell-side desks store every trade as a structured record
(internal "term sheet object"). All downstream tooling — pricing, risk,
P&L, regulatory reporting — reads from that single record. We adopt the
same pattern at much smaller scale.

A note on `borrow_spread_bps`:
  This is the stock borrow / repo spread implied by the equity forward
  curve, set by the security's repo market — NOT by the trade direction.
  Materiality, however, depends on trade direction:
    - AQ (client buys): dealer hedges LONG stock; no borrow needed for the
      dealer's economics, but b still enters the implied forward used for
      pricing.
    - DQ (client sells): dealer hedges SHORT stock and pays b directly; b
      is a primary dealer-margin component for hard-to-borrow names.
  For liquid US large-caps set 0; for HK small-caps query the desk.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import json

import yaml

from src.models.terms import AQDQTerms, AQDQSchedule, resolve_exchange_calendar
from src.models.schedule_loaders import read_ts_periods_csv, build_observation_schedule_from_ts
from src.engines.mc import MCSettings


# --------------------------------------------------------------------- #
# Scenario spec — what real-world drift to assume for one MC run
# --------------------------------------------------------------------- #

@dataclass
class ScenarioSpec:
    """One macro / drift scenario for the MC engine.

    - 'risk_neutral': drift = r - q - b from the discount curve (no override).
      This is the ONLY scenario suitable for fair-value / MTM / hedge-Greek use.
    - 'bull' / 'bear' / 'flat': drift_pct overrides the risk-neutral drift with
      a constant annualised log-drift (e.g. +0.20 = +20%/yr). Use for
      real-world tail-risk illustration only — never for valuation.

    DEALER NOTE: Tier-1 desks separate the "pricing book" (risk-neutral, marks
    & Greeks) from the "scenario book" (real-world, stress P&L). Mixing them
    is the single most common analytical error in buy-side accumulator decks —
    which is exactly the mistake the AI draft made by using +60% historical
    drift for fair-value MTM.
    """
    name: str
    drift_pct: Optional[float] = None   # None => risk-neutral (no override)


# --------------------------------------------------------------------- #
# Top-level trade config — what the YAML maps to
# --------------------------------------------------------------------- #

@dataclass
class TradeConfig:
    """Parsed YAML config, plus convenience accessors for the engine."""
    trade_id: str
    raw: Dict[str, Any]                       # original YAML dict, for debugging / round-trip

    # Underlying
    ticker: str
    exchange: str
    currency: str

    # Terms (already resolved to absolute prices, not % of spot)
    terms: AQDQTerms
    schedule: AQDQSchedule

    # Market inputs (None => auto-fetch / curve-driven)
    pricing_date: date
    spot: Optional[float]                         # pricing spot used by the MC engine
    reference_spot: Optional[float]               # contract spot used to resolve % strikes / KO
    flat_vol: float
    rate_curve: str                           # 'SOFR' | 'HIBOR' | 'flat'
    flat_rate: Optional[float]
    div_yield: Optional[float]
    borrow_spread_bps: float
    vol_input: str = "flat"                   # 'flat' (Phase 1) | 'surface' (Phase 2)
    vol_surface_path: Optional[str] = None
    vol_surface_model: Optional[str] = None
    vol_surface_params: Dict[str, Any] = field(default_factory=dict)
    schedule_source: str = "calendar"
    schedule_path: Optional[str] = None

    # MC settings
    mc: MCSettings = field(default_factory=MCSettings)
    settlement_mode: str = "daily"

    # Scenarios to run
    scenarios: List[ScenarioSpec] = field(default_factory=list)


# --------------------------------------------------------------------- #
# Loader
# --------------------------------------------------------------------- #

_OBS_FREQ_TO_BIZ_DAYS = {
    "daily":     1,
    "weekly":    5,    # 1 obs per 5 business days
    "bi-weekly": 10,
    "biweekly":  10,
    "monthly":   21,
    "quarterly": 63,
}


def _months_to_business_days(months: int, obs_freq: str) -> int:
    """Rough conversion: months → number of observation dates.

    Uses 21 business days / month and divides by obs frequency. Good enough
    for the engine — actual schedule is generated by QuantLib calendar.
    """
    total_biz_days = months * 21
    per_obs = _OBS_FREQ_TO_BIZ_DAYS.get(obs_freq, 1)
    return max(1, total_biz_days // per_obs)


def _ensure_date(v: Union[str, date]) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def _resolve_config_path(config_path: Path, raw_path: Optional[str]) -> Optional[str]:
    if raw_path is None:
        return None
    candidate = Path(str(raw_path)).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    for base in (config_path.parent, config_path.parent.parent, Path.cwd()):
        p = (base / candidate).resolve()
        if p.exists():
            return str(p)
    return str((config_path.parent / candidate).resolve())


def load_trade_config(path: Union[str, Path], *, spot_override: Optional[float] = None) -> TradeConfig:
    """Parse a YAML (or JSON) trade file into a TradeConfig.

    spot_override: if you want to test "what if spot moves to X" without editing
    the file. Most users pass None and let the engine fetch live spot.

    Reads YAML if the extension is .yml/.yaml, JSON if .json.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Trade config not found: {p}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yml", ".yaml"):
        raw = yaml.safe_load(text)
    elif p.suffix.lower() == ".json":
        raw = json.loads(text)
    else:
        raise ValueError(f"Unsupported trade-config extension: {p.suffix}")

    # ---- top-level ----
    trade_id = str(raw["trade_id"])

    # ---- underlying ----
    u = raw.get("underlying", {})
    ticker = str(u.get("ticker", "")).strip()
    exchange = str(u.get("exchange", "")).strip().upper()
    currency = str(u.get("currency", "USD")).upper()

    # ---- market block ----
    m = raw.get("market", {})
    pricing_date = _ensure_date(m.get("pricing_date", date.today()))
    spot_yaml = m.get("spot")
    spot_yaml_float = float(spot_yaml) if spot_yaml is not None else None
    spot = float(spot_override) if spot_override is not None else spot_yaml_float
    reference_spot = spot_yaml_float if spot_yaml_float is not None else spot
    flat_vol = float(m.get("flat_vol", 0.30))
    rate_curve = str(m.get("rate_curve", "SOFR")).upper()
    flat_rate = (float(m["flat_rate"]) if m.get("flat_rate") is not None else None)
    div_yield = (float(m["div_yield"]) if m.get("div_yield") is not None else None)
    borrow_spread_bps = float(m.get("borrow_spread_bps", 0.0))
    vol_input = str(m.get("vol_input", "flat")).lower()
    vol_surface_raw = m.get("vol_surface") or {}
    vol_surface_path = _resolve_config_path(p, vol_surface_raw.get("path"))
    vol_surface_model = (
        str(vol_surface_raw.get("model")).lower()
        if vol_surface_raw.get("model") is not None else None
    )
    vol_surface_params = {
        k: v for k, v in vol_surface_raw.items()
        if k not in {"path", "model"}
    }

    # ---- terms block ----
    t = raw["terms"]
    product_type = str(t["product_type"]).upper()        # 'AQ' | 'DQ'
    side = str(t.get("side", "buy")).lower()             # 'buy' | 'sell'
    tenor_months = int(t["tenor_months"])
    obs_freq = str(t.get("observation", "daily")).lower()
    shares_per_period = float(t["shares_per_period"])
    gear_ratio = int(t.get("gear_ratio", 2))
    guarantee_periods = int(t.get("guarantee_periods", 0))
    cap_shares = t.get("cap_shares")
    cap_shares = float(cap_shares) if cap_shares is not None else None

    # Strike & KO can be given as absolute or as % of spot.
    # If % is used we need spot to resolve them. If neither spot nor
    # absolute strike provided, raise — engine cannot price without a strike.
    strike_pct = t.get("strike_pct_of_spot")
    ko_pct = t.get("ko_pct_of_spot")
    strike_abs = t.get("strike")
    ko_abs = t.get("ko_level")

    if strike_abs is not None:
        forward_price = float(strike_abs)
    elif strike_pct is not None and reference_spot is not None:
        forward_price = float(reference_spot) * float(strike_pct)
    else:
        raise ValueError(
            f"Trade {trade_id}: must provide either terms.strike (absolute) "
            f"or both terms.strike_pct_of_spot AND market.spot."
        )

    if ko_abs is not None:
        ko_level = float(ko_abs)
    elif ko_pct is not None and reference_spot is not None:
        ko_level = float(reference_spot) * float(ko_pct)
    else:
        raise ValueError(
            f"Trade {trade_id}: must provide either terms.ko_level (absolute) "
            f"or both terms.ko_pct_of_spot AND market.spot."
        )

    # Number of observation periods (rough; QuantLib generates the actual schedule)
    max_obs_days = _months_to_business_days(tenor_months, obs_freq)

    # ---- build AQDQTerms ----
    # KO direction: buy AQ has KO when spot >= KO_level (above); DQ flips it.
    ko_dir = "above" if product_type == "AQ" else "below"
    lnbd_dir = "below" if product_type == "AQ" else "above"

    terms = AQDQTerms(
        product_type=product_type,
        side=side,
        currency=currency,
        forward_price=forward_price,
        ko_level=ko_level,
        shares_per_day=shares_per_period,
        max_obs_days=max_obs_days,
        gtd_days=guarantee_periods,
        aq_mode=str(t.get("aq_mode", "regular")).lower(),
        gear_ratio=gear_ratio,
        max_total_shares=cap_shares,
        enable_pnbd=bool(t.get("enable_pnbd", True)),
        ko_direction=ko_dir,
        lnbd_direction=lnbd_dir,
        ticker=ticker,
        exchange_hint=exchange or None,
    )

    # ---- build AQDQSchedule ----
    # Effective date defaults to pricing_date, or to the first CSV period when
    # an explicit term-sheet period table is supplied.
    sched_raw = raw.get("schedule") or {}
    schedule_source = str(sched_raw.get("source", "calendar")).lower()
    schedule_path = _resolve_config_path(p, sched_raw.get("path"))
    period_rows = None
    if schedule_source in {"term_sheet_periods_csv", "periods_csv", "csv"}:
        if schedule_path is None:
            raise ValueError(f"Trade {trade_id}: schedule.path is required for {schedule_source}")
        period_rows = read_ts_periods_csv(schedule_path)

    default_effective = period_rows[0]["start"] if period_rows else pricing_date
    default_final = period_rows[-1]["end"] if period_rows else pricing_date + timedelta(days=tenor_months * 30)
    effective_date = _ensure_date(t.get("effective_date", default_effective))
    final_accum_date = _ensure_date(t.get("final_accum_date", default_final))
    schedule = AQDQSchedule(
        effective_date=effective_date,
        final_accum_date=final_accum_date,
    )
    schedule.bind_market_conventions(terms)

    if period_rows is not None:
        schedule.explicit_schedule = build_observation_schedule_from_ts(schedule.calendar, period_rows)
        terms.max_obs_days = len(schedule.explicit_schedule)

    # If observation frequency is coarser than daily, build an explicit
    # schedule by taking every N-th business day. Without this, the engine
    # treats every business day as an observation date — silently turning a
    # bi-weekly contract into a daily one (and inflating KO probability
    # because daily monitoring gives many more chances to breach).
    per_obs_biz_days = _OBS_FREQ_TO_BIZ_DAYS.get(obs_freq, 1)
    if period_rows is None and per_obs_biz_days > 1:
        all_biz = schedule.observation_dates(terms)
        # Step from index 0 by per_obs_biz_days; this gives the first observation
        # at the start of week N+1 etc.
        sampled = all_biz[per_obs_biz_days - 1::per_obs_biz_days]
        # Guarantee we always have at least one observation (the final accum date)
        if sampled and sampled[-1] != all_biz[-1]:
            sampled.append(all_biz[-1])
        schedule.explicit_schedule = sampled
        # Update terms.max_obs_days to match the actually-sampled count
        terms.max_obs_days = len(sampled)

    # ---- MC settings ----
    mc_raw = raw.get("mc", {})
    mc = MCSettings(
        n_paths=int(mc_raw.get("n_paths", 200_000)),
        seed=int(mc_raw.get("seed", 20250301)),
    )
    settlement_mode = str(mc_raw.get("settlement_mode", "daily")).lower()

    # ---- scenarios ----
    scenarios_raw = raw.get("scenarios") or [{"name": "risk_neutral"}]
    scenarios = []
    for sc in scenarios_raw:
        scenarios.append(ScenarioSpec(
            name=str(sc["name"]),
            drift_pct=(float(sc["drift_pct"]) if sc.get("drift_pct") is not None else None),
        ))

    return TradeConfig(
        trade_id=trade_id,
        raw=raw,
        ticker=ticker,
        exchange=exchange,
        currency=currency,
        terms=terms,
        schedule=schedule,
        pricing_date=pricing_date,
        spot=spot,
        reference_spot=reference_spot,
        flat_vol=flat_vol,
        rate_curve=rate_curve,
        flat_rate=flat_rate,
        div_yield=div_yield,
        borrow_spread_bps=borrow_spread_bps,
        vol_input=vol_input,
        vol_surface_path=vol_surface_path,
        vol_surface_model=vol_surface_model,
        vol_surface_params=vol_surface_params,
        schedule_source=schedule_source,
        schedule_path=schedule_path,
        mc=mc,
        settlement_mode=settlement_mode,
        scenarios=scenarios,
    )
