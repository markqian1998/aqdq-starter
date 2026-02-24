# src/models/schedule_loaders.py
"""Utilities for building observation schedules from Bloomberg / term-sheet CSV exports."""

from __future__ import annotations
from datetime import date, timedelta
from typing import List, Dict, Iterable
import csv
import QuantLib as ql


def _bizdays_between(cal: ql.Calendar, start: date, end: date) -> List[date]:
    """Return all exchange business days in [start, end] inclusive."""
    out, d = [], start
    while d <= end:
        if cal.isBusinessDay(ql.Date(d.day, d.month, d.year)):
            out.append(d)
        d += timedelta(days=1)
    return out


def build_observation_schedule_from_ts(
    calendar: ql.Calendar,
    period_rows: Iterable[Dict[str, object]]
) -> List[date]:
    """Build the complete observation date list from term-sheet period rows.

    Parameters
    ----------
    calendar     : QuantLib exchange calendar (e.g. HongKong(), NYSE())
    period_rows  : iterable of dicts with keys {'start': date, 'end': date, 'days': int}
                   as returned by read_ts_periods_csv()

    Algorithm (aligned with typical bank TS conventions):
      1. Enumerate all exchange business days in [start, end].
      2. Take only the first 'days' of them (the TS specifies the exact count).
      3. Raise ValueError if fewer business days are found than 'days' requires —
         this indicates a mismatch between the TS and the calendar that needs
         manual review.

    Returns
    -------
    List[date]
        Concatenated observation dates across all periods (e.g. 245 dates for a 1-year HK AQ).
    """
    all_dates: List[date] = []
    for i, row in enumerate(period_rows, 1):
        s, e, n = row["start"], row["end"], int(row["days"])
        block = _bizdays_between(calendar, s, e)
        if len(block) < n:
            raise ValueError(
                f"Period {i} ({s} to {e}): only {len(block)} business days found, "
                f"but the term sheet requires {n}. Check calendar or TS dates."
            )
        all_dates.extend(block[:n])
    return all_dates


def read_ts_periods_csv(path: str) -> List[Dict[str, object]]:
    """Read a term-sheet period CSV and return a list of period dicts.

    Expected CSV format (columns: period, start, end, days; dates in YYYY-MM-DD):

        period,start,end,days
        1,2025-02-27,2025-03-12,10
        2,2025-03-13,2025-03-26,10
        ...

    Returns
    -------
    List of dicts with keys: {'start': date, 'end': date, 'days': int}
    """
    rows: List[Dict[str, object]] = []
    with open(path, "r", newline="", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            y1, m1, d1 = map(int, r["start"].split("-"))
            y2, m2, d2 = map(int, r["end"].split("-"))
            rows.append({
                "start": date(y1, m1, d1),
                "end":   date(y2, m2, d2),
                "days":  int(r["days"]),
            })
    return rows
