#!/usr/bin/env python3
# aqdq_cli.py
"""Command-line entry point for the aqdq_starter accumulator pricing tool.

Usage
-----
    python aqdq_cli.py price trades/avgo_demo.yaml
    python aqdq_cli.py price trades/avgo_demo.yaml --output-dir reports/
    python aqdq_cli.py price trades/avgo_demo.yaml --no-plot
    python aqdq_cli.py price trades/avgo_demo.yaml --spot 1320 --paths 50000

Reads a YAML / JSON trade config, runs every scenario defined in it under
Monte Carlo, writes a Markdown + JSON report, and (unless --no-plot) saves
the four standard charts.

Phase 1 scope. Future subcommands (Phase 3+):
    python aqdq_cli.py ingest-pdf TERMSHEET.pdf  # → trades/auto_*.yaml
    python aqdq_cli.py serve                     # local Flask UI
"""

from __future__ import annotations
import argparse
import sys
from datetime import date
from pathlib import Path

from src.io.trade_config import load_trade_config
from src.io.report import write_markdown_report, write_json_report
from src.analytics.multi_scenario import run_all_scenarios
from src.analytics.plots import save_all_plots
from src.market.snapshot import build_snapshot_auto, EquitySnapshot
from src.market.vol_surface import SLVParameterSurface
from src.engines.payoff import AQDQRuntimeState


# --------------------------------------------------------------------- #
# Subcommand: price
# --------------------------------------------------------------------- #

def _cmd_price(args: argparse.Namespace) -> int:
    """Price a single trade config and write reports + (optionally) plots."""
    cfg = load_trade_config(args.config, spot_override=args.spot)

    # Override n_paths from CLI if provided (handy for quick sanity checks)
    if args.paths is not None:
        cfg.mc.n_paths = int(args.paths)

    # Optional scenario filter (comma-separated names)
    if args.scenarios:
        wanted = set(s.strip() for s in args.scenarios.split(","))
        cfg.scenarios = [s for s in cfg.scenarios if s.name in wanted]
        if not cfg.scenarios:
            print(f"[error] no scenarios match filter {args.scenarios!r}", file=sys.stderr)
            return 2

    # --- Build market snapshot ---
    # Auto-curve path (SOFR / HIBOR) unless flat_rate explicitly given
    if cfg.rate_curve == "FLAT" or cfg.flat_rate is not None:
        mkt = EquitySnapshot.flat(
            today=cfg.pricing_date,
            spot=cfg.spot if cfg.spot is not None else 0.0,
            vol=cfg.flat_vol,
            r=float(cfg.flat_rate or 0.0),
            q=float(cfg.div_yield or 0.0),
            market=("HK" if cfg.exchange == "HK" else "US"),
            curve_ccy=("HKD" if cfg.exchange == "HK" else "USD"),
            borrow_spread_bps=cfg.borrow_spread_bps,
        )
    else:
        mkt = build_snapshot_auto(
            ticker=cfg.ticker,
            today=cfg.pricing_date,
            vol=cfg.flat_vol,
            exchange_hint=cfg.exchange or None,
            spot_override=cfg.spot,
            override_div_yield=cfg.div_yield,
            borrow_spread_bps=cfg.borrow_spread_bps,
        )

    vol_source = "flat"
    if cfg.vol_input in {"slv", "surface", "local_vol", "slv_param_curve"}:
        if not cfg.vol_surface_path:
            print("[error] market.vol_surface.path is required when vol_input is slv", file=sys.stderr)
            return 2
        surface = SLVParameterSurface.from_csv(
            cfg.vol_surface_path,
            today=cfg.pricing_date,
            put_moneyness=float(cfg.vol_surface_params.get("put_moneyness", 0.80)),
            call_moneyness=float(cfg.vol_surface_params.get("call_moneyness", 1.20)),
        )
        mkt = mkt.with_vol_surface(surface)
        mkt.vol = surface.atm_vol_at(1.0)
        vol_source = cfg.vol_surface_model or "slv_param_curve"

    rt = AQDQRuntimeState(today=cfg.pricing_date, delivered_to_date=0.0)

    print(f"[info] {cfg.trade_id}: spot={mkt.spot:.2f}, "
          f"vol={mkt.vol:.2%}, "
          f"vol_source={vol_source}, "
          f"drift(r-q-b)={mkt.fwd_drift(t=1.0):.2%}, "
          f"borrow={mkt.borrow_spread_bps:.0f}bps, "
          f"n_paths={cfg.mc.n_paths:,}, "
          f"scenarios={[s.name for s in cfg.scenarios]}",
          file=sys.stderr)

    # --- Run all scenarios ---
    results = run_all_scenarios(
        terms=cfg.terms,
        schedule=cfg.schedule,
        mkt=mkt,
        rt=rt,
        settings=cfg.mc,
        scenarios=cfg.scenarios,
        settlement_mode=cfg.settlement_mode,
    )

    # --- Reports & plots ---
    # Each run gets ONE self-contained folder so reports/ never turns into a
    # flat pile of loose files. Layout:
    #   reports/<run_name>/
    #       report.md
    #       report.json
    #       plots/<scenario>/<plot>.png
    out_dir = Path(args.output_dir).expanduser().resolve()
    stamp = cfg.pricing_date.strftime("%Y%m%d")
    # Canonical run name: TICKER_<PRODUCT>_YYYYMMDD (e.g. BE_AQ_20260522).
    # Derived from the trade's own fields — not the free-text trade_id — so the
    # folder name is always consistent regardless of how trade_id was written.
    ticker_clean = "".join(ch for ch in cfg.ticker.upper() if ch.isalnum())
    run_name = f"{ticker_clean}_{cfg.terms.product_type}_{stamp}"
    run_dir = out_dir / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    md_target = str(run_dir / "report.md")
    md_path = write_markdown_report(
        cfg, results, md_target,
        plot_files=None,  # filled in below if plots are generated
    )
    json_path = write_json_report(cfg, results, str(run_dir / "report.json"))

    plot_paths_per_scenario = {}
    if not args.no_plot:
        for name, r in results.items():
            sc_dir = run_dir / "plots" / name
            plot_paths_per_scenario[name] = save_all_plots(r, str(sc_dir))
        # Rewrite Markdown with plot embeds (paths relative to the report file,
        # which now lives inside run_dir alongside the plots/ folder).
        relpaths = {
            name: {k: str(Path(v).relative_to(run_dir)) for k, v in pf.items()}
            for name, pf in plot_paths_per_scenario.items()
        }
        md_path = write_markdown_report(cfg, results, md_target, plot_files=relpaths)

    print(f"[ok] wrote {run_dir}/")
    print(f"[ok]   report.md + report.json"
          + (f" + {sum(len(v) for v in plot_paths_per_scenario.values())} plots" if plot_paths_per_scenario else ""))

    # --- Console summary (one line per scenario) ---
    print()
    print(f"=== {cfg.trade_id} summary ===")
    for name, r in results.items():
        print(f"  {name:>14s}: KO={r.ko_probability:6.2%}  "
              f"E[shares]={r.expected_shares:>8,.0f}  "
              f"p95={r.p95_shares:>8,.0f}  "
              f"PV={r.pv:>12,.0f}")
    return 0


# --------------------------------------------------------------------- #
# Argparser
# --------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aqdq",
        description="Accumulator/Decumulator MC pricing & risk CLI",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_price = sub.add_parser("price", help="Price a trade config (YAML/JSON)")
    p_price.add_argument("config", help="Path to trade config (.yaml or .json)")
    p_price.add_argument("--output-dir", default="reports",
                         help="Directory for reports + plots (default: reports/)")
    p_price.add_argument("--no-plot", action="store_true",
                         help="Skip plot generation (faster)")
    p_price.add_argument("--spot", type=float, default=None,
                         help="Override spot price (useful for what-ifs)")
    p_price.add_argument("--paths", type=int, default=None,
                         help="Override Monte Carlo path count")
    p_price.add_argument("--scenarios", default=None,
                         help="Comma-separated scenario names to run "
                              "(filters the config's scenario list)")
    p_price.set_defaults(func=_cmd_price)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
