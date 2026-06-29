# src/io/report.py
"""Generate Markdown + JSON reports from scenario results.

The Markdown structure intentionally mirrors the 11-section template the
boss used for the NVDA AI draft, so reviewers see a familiar layout but
with corrected methodology (separate risk-neutral vs real-world drift
columns; tail-risk table at the front; Greeks only under risk-neutral).
"""

from __future__ import annotations
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional
import json

from src.io.trade_config import TradeConfig
from src.analytics.scenarios import ScenarioResult


# --------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------- #

def write_json_report(
    cfg: TradeConfig,
    results: Dict[str, ScenarioResult],
    out_path: str,
) -> str:
    """Write a complete JSON record: trade config + all scenario results.

    Suitable for re-import into Airtable, web UI, or downstream analytics.
    """
    payload: Dict[str, Any] = {
        "trade_id": cfg.trade_id,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "trade_config": cfg.raw,
        "scenarios": {name: r.to_dict() for name, r in results.items()},
    }
    p = Path(out_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------- #
# Markdown helpers
# --------------------------------------------------------------------- #

def _fmt_pct(x: float) -> str:
    return f"{x:.2%}"

def _fmt_num(x: float, decimals: int = 2) -> str:
    return f"{x:,.{decimals}f}"

def _fmt_int(x: float) -> str:
    return f"{int(round(x)):,}"

def _fmt_money(x: float) -> str:
    if abs(x) >= 1_000_000:
        return f"${x/1_000_000:.2f}M"
    return f"${x:,.0f}"


def _risk_neutral_or_first(results: Dict[str, ScenarioResult]) -> ScenarioResult:
    if "risk_neutral" in results:
        return results["risk_neutral"]
    return next(iter(results.values()))


# --------------------------------------------------------------------- #
# Markdown report
# --------------------------------------------------------------------- #

def write_markdown_report(
    cfg: TradeConfig,
    results: Dict[str, ScenarioResult],
    out_path: str,
    plot_files: Optional[Dict[str, Dict[str, str]]] = None,
) -> str:
    """Write a Markdown report covering all scenarios.

    plot_files: optional {scenario_name: {plot_name: filepath}} for embedding
    images. Paths are relative-friendly: pass paths relative to the report's
    parent directory.
    """
    rn = _risk_neutral_or_first(results)
    lines: List[str] = []

    # ---- 1. Header / Executive Summary ----
    lines += [
        f"# Accumulator Risk Note — {cfg.trade_id}",
        "",
        f"**Underlying:** {cfg.ticker} ({cfg.exchange})  |  "
        f"**Currency:** {cfg.currency}  |  "
        f"**Pricing date:** {cfg.pricing_date.isoformat()}",
        "",
        "_Model-based risk illustration. Risk-neutral fair value and Greeks "
        "shown for hedging; real-world drift scenarios shown for buy-side "
        "stress only. Not a dealer mark, not investment advice._",
        "",
        "## 1. Executive Summary",
        "",
        f"- Product: **{cfg.terms.product_type}** ({cfg.terms.side}), tenor "
        f"≈ {len(rn.obs_dates)} observation periods, gear "
        f"{cfg.terms.gear_ratio}× below strike, guarantee "
        f"{cfg.terms.gtd_days} period(s).",
        f"- Strike: **{_fmt_num(cfg.terms.forward_price)}** "
        f"({cfg.raw.get('terms', {}).get('strike_pct_of_spot', '—')} of spot)  "
        f"|  KO: **{_fmt_num(cfg.terms.ko_level)}** "
        f"({cfg.raw.get('terms', {}).get('ko_pct_of_spot', '—')} of spot)",
        f"- Spot used: **{_fmt_num(cfg.spot or 0.0)}**  |  "
        f"Vol input: **{cfg.vol_input}**"
        f"{' (' + cfg.vol_surface_model + ')' if cfg.vol_surface_model else ''}  |  "
        f"Borrow spread: **{cfg.borrow_spread_bps:.0f} bps**",
        f"- Risk-neutral KO probability: **{_fmt_pct(rn.ko_probability)}**",
        f"- Risk-neutral expected shares: **{_fmt_int(rn.expected_shares)}**  |  "
        f"Median: **{_fmt_int(rn.median_shares)}**  |  "
        f"p95: **{_fmt_int(rn.p95_shares)}**",
        f"- Risk-neutral PV: **{_fmt_money(rn.pv)}** (±{_fmt_money(rn.std_err)})",
        f"- Median client P&L return: **{_fmt_pct(rn.median_pnl_return)}**  |  "
        f"P(PnL 80-100%): **{_fmt_pct(rn.pnl_return_buckets.get('80% to 100%', 0.0))}**  |  "
        f"P(PnL >100%): **{_fmt_pct(rn.pnl_return_buckets.get('> 100%', 0.0))}**",
        "",
    ]

    # ---- 2. Terms ----
    t = cfg.terms
    lines += [
        "## 2. Product Terms",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Trade ID | `{cfg.trade_id}` |",
        f"| Underlying | {cfg.ticker} ({cfg.exchange}) |",
        f"| Product type | {t.product_type} |",
        f"| Side | {t.side} |",
        f"| Strike | {_fmt_num(t.forward_price)} |",
        f"| KO level | {_fmt_num(t.ko_level)} |",
        f"| Shares per period | {_fmt_int(t.shares_per_day)} |",
        f"| Gear ratio | {t.gear_ratio}× |",
        f"| Guarantee periods | {t.gtd_days} |",
        f"| Cap shares | {_fmt_int(t.max_total_shares) if t.max_total_shares else '—'} |",
        f"| AQ mode | {t.aq_mode} |",
        f"| Settlement mode | {cfg.settlement_mode} |",
        f"| Effective date | {cfg.schedule.effective_date.isoformat()} |",
        f"| Final accum date | {cfg.schedule.final_accum_date.isoformat()} |",
        "",
    ]

    # ---- 3. Methodology ----
    lines += [
        "## 3. Methodology",
        "",
        "- **Model:** Lognormal GBM with antithetic variance reduction "
        f"({rn.n_paths:,} paths).",
        f"- **Drift convention (risk-neutral):** r − q − b, where b is the "
        f"borrow / repo spread ({cfg.borrow_spread_bps:.0f} bps for this trade).",
        f"- **Vol input:** `{cfg.vol_input}`. Flat mode uses one scalar IV; "
        "SLV parameter mode uses expiry-interpolated ATM forward and "
        "put/ATM/call vol anchors as a bounded local-vol proxy. A full "
        "production build would replace this with calibrated SVI + Dupire "
        "local vol or full SLV.",
        "- **Greeks:** finite-difference bump-and-reprice with common random "
        "numbers (delta ±1%, vega ±1 vol point).",
        f"- **Settlement:** `{cfg.settlement_mode}` "
        f"({'mark each obs day' if cfg.settlement_mode == 'daily' else 'lump at maturity'}).",
        "",
    ]

    # ---- 4. Headline results table (multi-scenario) ----
    lines += [
        "## 4. Headline Results — All Scenarios",
        "",
        "| Metric | " + " | ".join(name for name in results) + " |",
        "|---|" + "|".join("---" for _ in results) + "|",
        _row("Drift used (annualised)",       results, lambda r: _fmt_pct(r.drift_used)),
        _row("KO probability",                results, lambda r: _fmt_pct(r.ko_probability)),
        _row("Expected KO period",            results, lambda r: f"{r.expected_ko_period:.1f}" if r.expected_ko_period is not None else "—"),
        _row("Expected shares",               results, lambda r: _fmt_int(r.expected_shares)),
        _row("Median shares",                 results, lambda r: _fmt_int(r.median_shares)),
        _row("p95 shares",                    results, lambda r: _fmt_int(r.p95_shares)),
        _row("p99 shares",                    results, lambda r: _fmt_int(r.p99_shares)),
        _row("PV (use only risk-neutral)",    results, lambda r: _fmt_money(r.pv)),
        _row("Expected MTM",                  results, lambda r: _fmt_money(r.expected_mtm)),
        _row("p5 MTM",                        results, lambda r: _fmt_money(r.p5_mtm)),
        _row("p95 MTM",                       results, lambda r: _fmt_money(r.p95_mtm)),
        _row("P(terminal loss)",              results, lambda r: _fmt_pct(r.prob_terminal_loss)),
        _row("Median client P&L return",      results, lambda r: _fmt_pct(r.median_pnl_return)),
        _row("P(PnL 80-100%)",                results, lambda r: _fmt_pct(r.pnl_return_buckets.get("80% to 100%", 0.0))),
        _row("P(PnL >100%)",                  results, lambda r: _fmt_pct(r.pnl_return_buckets.get("> 100%", 0.0))),
        "",
        "_PV / Greeks are only meaningful under the risk-neutral column. "
        "Bull / bear columns are real-world stress illustrations; their PV is "
        "**not** a fair value._",
        "",
    ]

    # ---- 5. Risk-neutral Greeks ----
    lines += [
        "## 5. Risk-Neutral Greeks",
        "",
        "| Greek | Value | Bump |",
        "|---|---|---|",
        f"| Delta | {_fmt_num(rn.greeks['delta'], 4)} | ±1% spot |",
        f"| Gamma | {_fmt_num(rn.greeks['gamma'], 6)} | ±1% spot |",
        f"| Vega  | {_fmt_num(rn.greeks['vega'], 2)} | ±1 vol pt |",
        "",
    ]

    # ---- 6. Tail-risk table (per scenario) ----
    lines += ["## 6. Tail-Risk Table (Buy-Side View)", ""]
    for name, r in results.items():
        lines += [f"### {name}", "",
                  "| Threshold | Shares | Probability | Cash outlay (strike) |",
                  "|---|---|---|---|"]
        for row in r.tail_risk_table:
            lines.append(
                f"| ≥{row.threshold_pct_of_cap:.0%} of cap "
                f"| {_fmt_int(row.threshold_shares)} "
                f"| {_fmt_pct(row.probability)} "
                f"| {_fmt_money(row.cash_outlay_at_strike)} |"
            )
        lines.append("")

    # ---- 7. KO timing ----
    lines += ["## 7. KO Timing Distribution", ""]
    for name, r in results.items():
        if r.expected_ko_period is None:
            lines += [f"- **{name}:** No KO observed in {r.n_paths:,} paths.", ""]
        else:
            lines += [
                f"- **{name}:** KO probability {_fmt_pct(r.ko_probability)}, "
                f"expected KO period {r.expected_ko_period:.1f}, "
                f"median KO period {r.median_ko_period}.",
            ]
    lines.append("")

    # ---- 8. Client P&L return buckets ----
    lines += ["## 8. Client P&L Return Distribution", ""]
    lines += [
        "Pathwise return is defined as discounted P&L divided by discounted "
        "strike cash outlay for the shares accumulated on that path.",
        "",
    ]
    for name, r in results.items():
        lines += [f"### {name}", "", "| P&L return bucket | Probability |", "|---|---|"]
        for label, prob in r.pnl_return_buckets.items():
            lines.append(f"| {label} | {_fmt_pct(prob)} |")
        lines += [
            "",
            f"- Expected return: **{_fmt_pct(r.expected_pnl_return)}**; "
            f"median: **{_fmt_pct(r.median_pnl_return)}**; "
            f"p5/p95: **{_fmt_pct(r.p5_pnl_return)} / {_fmt_pct(r.p95_pnl_return)}**.",
            "",
        ]

    # ---- 9. Embedded plots ----
    if plot_files:
        lines += ["## 9. Plots", ""]
        for name, pf in plot_files.items():
            lines.append(f"### {name}")
            lines.append("")
            for label, path in pf.items():
                lines.append(f"**{label}**")
                lines.append("")
                lines.append(f"![{label}]({path})")
                lines.append("")

    # ---- 10. Analyst follow-up ----
    lines += [
        "## 10. Analyst Follow-Up",
        "",
        "- Phase 2: replace flat vol with SVI surface and Dupire local vol "
        "(captures skew → corrects KO probability).",
        "- Sensitivity grid: rerun with spot ±5/10%, vol ±2/5 vol pts.",
        "- Borrow-cost calibration: confirm desk-quoted borrow vs the "
        f"{cfg.borrow_spread_bps:.0f} bps used here.",
        "- Liquidity / concentration test: aggregate `p95_shares × strike` "
        "across the accumulator book to size cash buffer.",
        "- Compare to alternatives: cash-secured puts, limit-order program, "
        "vertical put-spread, staggered outrights.",
        "",
    ]

    # ---- 11. Bottom line ----
    lines += [
        "## 11. Bottom Line",
        "",
        f"Under the risk-neutral model, this {t.product_type} on {cfg.ticker} "
        f"has a **{_fmt_pct(rn.ko_probability)} KO probability**, "
        f"**{_fmt_int(rn.expected_shares)} expected shares**, and a "
        f"**{_fmt_pct(rn.tail_risk_table[-1].probability)} probability of "
        f"reaching the cap "
        f"({_fmt_int(rn.tail_risk_table[-1].threshold_shares)} shares, "
        f"{_fmt_money(rn.tail_risk_table[-1].cash_outlay_at_strike)} at strike).**",
        "",
        "Decision framework: the trade is appropriate when the client both "
        "(a) wants to accumulate the underlying as a long-term holding and "
        "(b) can fund the worst-case forced delivery in the bear-drift "
        "scenario without portfolio dislocation. If either fails, prefer a "
        "structure with explicit downside protection.",
        "",
    ]

    out = Path(out_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return str(out)


def _row(label: str, results: Dict[str, ScenarioResult], fn) -> str:
    return "| " + label + " | " + " | ".join(fn(r) for r in results.values()) + " |"
