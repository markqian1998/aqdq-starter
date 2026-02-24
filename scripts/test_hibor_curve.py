# scripts/test_hibor_curve.py
"""Fetch the live TMA HIBOR curve and inspect discount factors."""

from datetime import date
import numpy as np
import QuantLib as ql

from src.market.feeds.hibor_tma import fetch_hibor_today_tma, build_discount_curve_from_tma_deposits
from src.market.snapshot import EquitySnapshot

if __name__ == "__main__":
    today = date.today()

    # 1) Fetch TMA HIBOR fixings for today (or the most recent available date)
    row = fetch_hibor_today_tma(asof=today)
    print("TMA HIBOR fixings (decimal):")
    print(row.dropna())

    # 2) Bootstrap a QuantLib discount curve from the deposit rates
    curve = build_discount_curve_from_tma_deposits(today, row)

    # 3) Build a snapshot (spot / div are placeholders here; we just want to test df())
    snap = EquitySnapshot(
        today=today,
        spot=50.0,
        vol=0.47274,
        flat_r=float(row.get("3M", 0.02)),  # used as fallback if curve bootstrap fails
        div_yield=0.0,
        discount_curve=curve
    )

    # 4) Print discount factors at selected maturities
    for T in [1 / 12, 0.25, 0.5, 1.0]:
        print(f"DF({T:.4f}y) = {snap.df(T):.6f}")

    # To use this snapshot in the pricer, pass it via build_snapshot_auto()
    # or construct EquitySnapshot directly and pass to price_aqdq_mc().
