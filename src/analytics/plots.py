# src/analytics/plots.py
"""Four standard plots for an accumulator trade report.

All plot functions take a single ScenarioResult and return a
matplotlib.figure.Figure. The caller decides whether to savefig or show —
this keeps the plotting layer pure and testable.

Plots:
  1. sample_paths_plot — 100 GBM paths + KO barrier + strike + KO markers
  2. ko_timing_plot    — histogram of KO observation index
  3. shares_distribution_plot — histogram of total shares accumulated
  4. tail_risk_plot    — bar chart of P(shares >= threshold)

Style choices
-------------
- Single colour palette (no rainbow) — dealer reports use 1-2 accent colours.
- Strike and KO barriers always drawn as horizontal reference lines.
- Vertical reference lines for expected / median / p95 on share distribution.
- All text is in plain English (no jargon abbreviations in chart text).
"""

from __future__ import annotations
from typing import Optional

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.figure as mpl_fig

from src.analytics.scenarios import ScenarioResult


# A neutral colour palette suitable for B&W printing as well as on-screen viewing.
_COLOR_PRIMARY   = "#1f4e79"   # deep blue — main series
_COLOR_BARRIER   = "#c00000"   # red — KO barrier
_COLOR_STRIKE    = "#2e7d32"   # green — strike
_COLOR_KO_DOT    = "#d97706"   # amber — KO markers
_COLOR_NEUTRAL   = "#888888"   # grey — secondary lines


def sample_paths_plot(result: ScenarioResult, *, max_paths: int = 100) -> mpl_fig.Figure:
    """Plot a representative sample of simulated price paths.

    Paths are overlaid on horizontal lines for strike and KO. Any sample
    path that knocked out is marked with an amber dot at the KO observation.
    """
    fig, ax = plt.subplots(figsize=(10, 5.5))

    n_to_plot = min(max_paths, result.sample_paths.shape[0])
    times = result.times_yr

    for i in range(n_to_plot):
        ax.plot(
            times, result.sample_paths[i, :],
            color=_COLOR_PRIMARY, alpha=0.10, linewidth=0.8,
        )
        ko = int(result.sample_path_ko_idx[i])
        if ko >= 0 and ko < len(times):
            ax.plot(times[ko], result.sample_paths[i, ko], "o",
                    color=_COLOR_KO_DOT, markersize=3.0, alpha=0.6)

    ax.axhline(result.ko_level, color=_COLOR_BARRIER, linestyle="--",
               linewidth=1.5, label=f"KO barrier ({result.ko_level:.2f})")
    ax.axhline(result.forward_price, color=_COLOR_STRIKE, linestyle="--",
               linewidth=1.5, label=f"Strike ({result.forward_price:.2f})")

    ax.set_xlabel("Time (years)")
    ax.set_ylabel(f"{result.underlying or 'Underlying'} price")
    ax.set_title(f"Simulated price paths — {result.scenario_name} "
                 f"(n={n_to_plot} of {result.n_paths:,})")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    return fig


def ko_timing_plot(result: ScenarioResult) -> mpl_fig.Figure:
    """Histogram of KO observation index (probability mass per period).

    The 'no KO' bucket is shown separately on the far right.
    """
    dist = {int(k): float(v) for k, v in result.ko_timing_distribution.items()}
    no_ko_p = dist.get(-1, 0.0)
    ko_keys = sorted(k for k in dist.keys() if k >= 0)
    ko_probs = [dist[k] for k in ko_keys]

    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.bar(ko_keys, ko_probs, color=_COLOR_PRIMARY, alpha=0.85, width=0.85)
    # Highlight no-KO bucket
    if ko_keys:
        max_x = max(ko_keys)
        no_ko_x = max_x + 2
    else:
        no_ko_x = 1
    ax.bar([no_ko_x], [no_ko_p], color=_COLOR_NEUTRAL, alpha=0.85, width=0.85,
           label="No KO")
    ax.axhline(0, color="black", linewidth=0.5)

    ax.set_xlabel("Observation period index")
    ax.set_ylabel("Probability")
    ax.set_title(f"KO timing distribution — {result.scenario_name} "
                 f"(KO prob: {result.ko_probability:.1%})")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    return fig


def shares_distribution_plot(result: ScenarioResult) -> mpl_fig.Figure:
    """Histogram of total shares accumulated per path.

    Vertical lines mark expected, median, and 95th percentile so the
    asymmetry is visually obvious.
    """
    fig, ax = plt.subplots(figsize=(10, 4.5))

    edges = []
    probs = []
    for k, v in result.shares_histogram.items():
        lo_str, hi_str = k.split("|")
        edges.append((float(lo_str), float(hi_str)))
        probs.append(float(v))
    if not edges:
        ax.set_title("Shares distribution (empty)")
        return fig

    centers = [(lo + hi) / 2 for lo, hi in edges]
    widths = [(hi - lo) * 0.95 for lo, hi in edges]
    ax.bar(centers, probs, width=widths, color=_COLOR_PRIMARY, alpha=0.85)

    # Vertical reference lines
    for label, value, color, ls in [
        ("Expected", result.expected_shares, _COLOR_STRIKE, "-"),
        ("Median",   result.median_shares,   _COLOR_NEUTRAL, "--"),
        ("p95",      result.p95_shares,      _COLOR_BARRIER, ":"),
    ]:
        ax.axvline(value, color=color, linestyle=ls, linewidth=1.5,
                   label=f"{label} ({value:,.0f})")

    ax.set_xlabel("Total shares accumulated")
    ax.set_ylabel("Probability")
    ax.set_title(f"Share-accumulation distribution — {result.scenario_name}")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    return fig


def tail_risk_plot(result: ScenarioResult) -> mpl_fig.Figure:
    """Bar chart of P(shares >= threshold) at canonical %-of-cap levels."""
    rows = result.tail_risk_table
    if not rows:
        fig, ax = plt.subplots()
        ax.set_title("Tail risk (no data)")
        return fig

    labels = [f"≥{r.threshold_pct_of_cap:.0%}\n({r.threshold_shares:,.0f} sh)" for r in rows]
    probs = [r.probability for r in rows]
    cash = [r.cash_outlay_at_strike for r in rows]

    fig, ax1 = plt.subplots(figsize=(10, 4.5))
    bars = ax1.bar(labels, probs, color=_COLOR_PRIMARY, alpha=0.85, width=0.6)
    ax1.set_ylabel("Probability")
    ax1.set_ylim(0, max(probs) * 1.25 if probs else 1.0)
    ax1.set_xlabel("Share threshold (% of cap, absolute shares)")
    ax1.set_title(f"Tail-risk table — {result.scenario_name}")
    ax1.grid(alpha=0.3, axis="y")

    # Annotate each bar with probability + cash outlay
    for bar, p, c in zip(bars, probs, cash):
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + max(probs) * 0.02,
            f"{p:.1%}\n${c:,.0f}",
            ha="center", va="bottom", fontsize=9,
        )

    fig.tight_layout()
    return fig


def pnl_return_buckets_plot(result: ScenarioResult) -> mpl_fig.Figure:
    """Bar chart of client P&L return buckets.

    Return is pathwise P&L divided by strike cash outlay, so 100% means the
    path made profit equal to the cash used to buy accumulated shares.
    """
    buckets = getattr(result, "pnl_return_buckets", {}) or {}
    fig, ax = plt.subplots(figsize=(11, 4.8))
    if not buckets:
        ax.set_title("Client P&L return distribution (no data)")
        return fig

    labels = list(buckets.keys())
    probs = [float(v) for v in buckets.values()]
    colors = [
        _COLOR_BARRIER if label.startswith("<") or label.startswith("-") else _COLOR_PRIMARY
        for label in labels
    ]
    bars = ax.bar(labels, probs, color=colors, alpha=0.88, width=0.72)
    ax.set_ylabel("Probability")
    ax.set_xlabel("Path P&L / strike cash outlay")
    ax.set_title(f"Client P&L return distribution — {result.scenario_name}")
    ax.set_ylim(0, max(probs) * 1.25 if probs else 1.0)
    ax.grid(alpha=0.3, axis="y")
    ax.tick_params(axis="x", rotation=25)
    for bar, p in zip(bars, probs):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + (max(probs) * 0.02 if probs else 0.01),
            f"{p:.1%}",
            ha="center", va="bottom", fontsize=9,
        )
    fig.tight_layout()
    return fig


# --------------------------------------------------------------------- #
# Convenience: save all four plots for one scenario to a directory
# --------------------------------------------------------------------- #

def save_all_plots(result: ScenarioResult, out_dir: str, *, dpi: int = 130) -> dict:
    """Save the four standard plots as PNGs. Returns {plot_name: file_path}.

    File names: paths.png, ko_timing.png, shares_dist.png, tail_risk.png,
    pnl_return.png.
    """
    from pathlib import Path
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    paths = {
        "paths":       out / "paths.png",
        "ko_timing":   out / "ko_timing.png",
        "shares_dist": out / "shares_dist.png",
        "tail_risk":   out / "tail_risk.png",
        "pnl_return":  out / "pnl_return.png",
    }

    figs = {
        "paths":       sample_paths_plot(result),
        "ko_timing":   ko_timing_plot(result),
        "shares_dist": shares_distribution_plot(result),
        "tail_risk":   tail_risk_plot(result),
        "pnl_return":  pnl_return_buckets_plot(result),
    }
    for name, fig in figs.items():
        fig.savefig(paths[name], dpi=dpi, bbox_inches="tight")
        plt.close(fig)

    return {k: str(v) for k, v in paths.items()}
