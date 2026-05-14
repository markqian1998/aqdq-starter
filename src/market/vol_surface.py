# src/market/vol_surface.py
"""Volatility-parameter surface utilities.

The CSV used here is a compact dealer-style volatility parameter report:
expiry, ATM forward, ATM vol, downside put vol, upside call vol, and smile
shape parameters. It is not a full calibrated local-vol grid. For AQ/DQ
risk, we convert it into a bounded smile-aware local-vol proxy:

  - interpolate term structure by expiry;
  - interpolate spot-state vol by forward moneyness between put/ATM/call
    anchors;
  - use flat extrapolation beyond the anchors to avoid unstable wings;
  - compute vega with a parallel surface bump.

This is intentionally conservative and explicit. A later production upgrade
can replace the proxy with calibrated SVI + Dupire local vol or full SLV.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import List
import csv

import numpy as np


@dataclass(frozen=True)
class SLVParameterSlice:
    expiry: date
    t: float
    atm_fwd: float
    atm_vol: float
    put_vol: float
    call_vol: float
    skew: float
    smile: float
    call_wing: float
    put_wing: float


@dataclass
class SLVParameterSurface:
    """Bounded local-vol proxy built from dealer volatility parameters."""

    today: date
    slices: List[SLVParameterSlice]
    put_moneyness: float = 0.80
    call_moneyness: float = 1.20
    min_vol: float = 0.01
    max_vol: float = 3.00

    @classmethod
    def from_csv(
        cls,
        path: str | Path,
        *,
        today: date,
        put_moneyness: float = 0.80,
        call_moneyness: float = 1.20,
    ) -> "SLVParameterSurface":
        p = Path(path).expanduser()
        rows: List[SLVParameterSlice] = []
        with p.open("r", newline="", encoding="utf-8-sig") as f:
            rdr = csv.DictReader(f)
            for raw in rdr:
                expiry = _parse_expiry(raw["Expiry"])
                t = max((expiry - today).days / 365.0, 1.0 / 365.0)
                rows.append(SLVParameterSlice(
                    expiry=expiry,
                    t=t,
                    atm_fwd=_parse_float(raw["ATM Fwd"]),
                    atm_vol=_parse_vol(raw["ATM Vol"]),
                    put_vol=_parse_vol(raw["Put Vol"]),
                    call_vol=_parse_vol(raw["Call Vol"]),
                    skew=_parse_float(raw.get("Skew", 0.0)),
                    smile=_parse_float(raw.get("Smile", 0.0)),
                    call_wing=_parse_float(raw.get("Call Wing", 0.0)),
                    put_wing=_parse_float(raw.get("Put Wing", 0.0)),
                ))

        if not rows:
            raise ValueError(f"No SLV volatility rows found in {p}")

        rows.sort(key=lambda r: r.t)
        return cls(
            today=today,
            slices=rows,
            put_moneyness=float(put_moneyness),
            call_moneyness=float(call_moneyness),
        )

    def atm_vol_at(self, t: float) -> float:
        return float(self._interp(t, "atm_vol"))

    def atm_fwd_at(self, t: float) -> float:
        return float(self._interp(t, "atm_fwd"))

    def local_vol(self, t: float, spot, *, parallel_bump: float = 0.0):
        """Return local vol for the path step at time t and current spot.

        The state variable is forward moneyness S/F(t). Downside states use
        the put-vol anchor, ATM states use ATM vol, and upside/KO states use
        the call-vol anchor. This mirrors how structuring desks first map a
        sparse smile parameter report onto path-dependent payoff risk before a
        full local-vol calibration is available.
        """
        s = np.asarray(spot, dtype=float)
        fwd = max(float(self._interp(t, "atm_fwd")), 1e-12)
        atm = float(self._interp(t, "atm_vol"))
        put = float(self._interp(t, "put_vol"))
        call = float(self._interp(t, "call_vol"))

        m = s / fwd
        pm = self.put_moneyness
        cm = self.call_moneyness

        downside = put + (atm - put) * ((m - pm) / max(1.0 - pm, 1e-12))
        upside = atm + (call - atm) * ((m - 1.0) / max(cm - 1.0, 1e-12))
        vol = np.where(m <= 1.0, downside, upside)
        vol = np.where(m <= pm, put, vol)
        vol = np.where(m >= cm, call, vol)
        vol = np.clip(vol + parallel_bump, self.min_vol, self.max_vol)

        if np.isscalar(spot):
            return float(vol)
        return vol

    def _interp(self, t: float, field: str) -> float:
        xs = np.array([r.t for r in self.slices], dtype=float)
        ys = np.array([float(getattr(r, field)) for r in self.slices], dtype=float)
        return float(np.interp(float(t), xs, ys, left=ys[0], right=ys[-1]))


def _parse_expiry(value: str) -> date:
    text = str(value).strip().replace("Sept", "Sep")
    return datetime.strptime(text, "%d %b %Y").date()


def _parse_float(value) -> float:
    text = str(value).strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1]
    if text in {"", "-", "None", "nan"}:
        return 0.0
    return float(text)


def _parse_vol(value) -> float:
    text = str(value).strip()
    if text.endswith("%"):
        return _parse_float(text) / 100.0
    raw = _parse_float(text)
    return raw / 100.0 if raw > 3.0 else raw
