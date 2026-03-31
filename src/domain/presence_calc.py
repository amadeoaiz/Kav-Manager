"""
Presence-fraction calculations for fairness weighting.

Provides utilities to compute what fraction of a day/night domain window
each soldier is present for, enabling presence-weighted averages.
"""
from __future__ import annotations

from datetime import datetime, date, time, timedelta
from typing import Sequence

from sqlalchemy.orm import Session

from src.core.models import PresenceInterval


def _overlap_hours(intervals: Sequence[PresenceInterval],
                   win_start: datetime, win_end: datetime) -> float:
    """Total hours of PRESENT intervals overlapping [win_start, win_end)."""
    total = 0.0
    for iv in intervals:
        if iv.status != "PRESENT":
            continue
        s = max(iv.start_time, win_start)
        e = min(iv.end_time, win_end)
        if e > s:
            total += (e - s).total_seconds() / 3600.0
    return total


def domain_windows(ref_date: date, night_start: int, night_end: int
                   ) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """Return (day_window, night_window) as (start_dt, end_dt) pairs.

    Day window:  [night_end on ref_date  ..  night_start on ref_date]
    Night window: [night_start on ref_date  ..  night_end on ref_date+1]
    """
    day_win_start = datetime.combine(ref_date, time(night_end, 0))
    day_win_end = datetime.combine(ref_date, time(night_start, 0))
    night_win_start = datetime.combine(ref_date, time(night_start, 0))
    night_win_end = datetime.combine(ref_date + timedelta(days=1), time(night_end, 0))
    return (day_win_start, day_win_end), (night_win_start, night_win_end)


def domain_window_hours(night_start: int, night_end: int) -> tuple[float, float]:
    """Return (day_domain_hours, night_domain_hours)."""
    # Standard wrap-around: night_end < night_start (e.g. 7 < 23)
    day_hours = float(night_start - night_end)        # e.g. 23-7 = 16
    night_hours = float(24 - night_start + night_end)  # e.g. 24-23+7 = 8
    return day_hours, night_hours


def compute_domain_fractions(
    db: Session,
    soldier_ids: list[int],
    ref_date: date,
    night_start: int,
    night_end: int,
) -> tuple[dict[int, float], dict[int, float]]:
    """Compute per-soldier presence fractions for day and night domains.

    Returns (day_fracs, night_fracs) where each maps soldier_id -> fraction
    in [0.0, 1.0].  A fraction of 1.0 means the soldier is present for the
    entire domain window; 0.0 means completely absent.

    Only soldiers whose ids are in soldier_ids are queried.
    """
    (day_s, day_e), (night_s, night_e) = domain_windows(ref_date, night_start, night_end)
    day_total, night_total = domain_window_hours(night_start, night_end)

    # Fetch all PRESENT intervals overlapping the full calendar day at once.
    cal_start = datetime.combine(ref_date, time(0, 0))
    cal_end = datetime.combine(ref_date + timedelta(days=1), time(night_end, 0))

    if not soldier_ids:
        return {}, {}

    all_ivs = (
        db.query(PresenceInterval)
        .filter(
            PresenceInterval.soldier_id.in_(soldier_ids),
            PresenceInterval.status == "PRESENT",
            PresenceInterval.start_time < cal_end,
            PresenceInterval.end_time > cal_start,
        )
        .all()
    )

    # Group by soldier.
    by_soldier: dict[int, list[PresenceInterval]] = {sid: [] for sid in soldier_ids}
    for iv in all_ivs:
        if iv.soldier_id in by_soldier:
            by_soldier[iv.soldier_id].append(iv)

    day_fracs: dict[int, float] = {}
    night_fracs: dict[int, float] = {}
    for sid in soldier_ids:
        ivs = by_soldier[sid]
        day_fracs[sid] = min(_overlap_hours(ivs, day_s, day_e) / day_total, 1.0) if day_total > 0 else 0.0
        night_fracs[sid] = min(_overlap_hours(ivs, night_s, night_e) / night_total, 1.0) if night_total > 0 else 0.0

    return day_fracs, night_fracs


def count_present_partial(
    db: Session,
    soldier_ids: list[int],
    ref_date: date,
) -> tuple[int, int]:
    """Count soldiers who are fully present vs partially present on ref_date.

    Full present = PRESENT intervals cover the entire calendar day [00:00, 00:00+1d).
    Partial = some PRESENT coverage but not full.
    Returns (full_count, partial_count).
    """
    cal_start = datetime.combine(ref_date, time(0, 0))
    cal_end = datetime.combine(ref_date + timedelta(days=1), time(0, 0))
    cal_hours = 24.0

    if not soldier_ids:
        return 0, 0

    all_ivs = (
        db.query(PresenceInterval)
        .filter(
            PresenceInterval.soldier_id.in_(soldier_ids),
            PresenceInterval.status == "PRESENT",
            PresenceInterval.start_time < cal_end,
            PresenceInterval.end_time > cal_start,
        )
        .all()
    )

    by_soldier: dict[int, list[PresenceInterval]] = {sid: [] for sid in soldier_ids}
    for iv in all_ivs:
        if iv.soldier_id in by_soldier:
            by_soldier[iv.soldier_id].append(iv)

    full = 0
    partial = 0
    for sid in soldier_ids:
        hours = _overlap_hours(by_soldier[sid], cal_start, cal_end)
        if hours >= cal_hours - 0.01:  # allow tiny float rounding
            full += 1
        elif hours > 0.01:
            partial += 1

    return full, partial


def compute_total_domain_presence(
    db: Session,
    soldier_ids: list[int],
    night_start: int,
    night_end: int,
) -> tuple[dict[int, float], dict[int, float]]:
    """Compute total day-domain and night-domain presence hours per soldier
    across ALL their PRESENT intervals.

    Used by resync_soldier_rates for the lifetime rate calculation.
    Returns (day_presence_hours, night_presence_hours) dicts.
    """
    day_total_h, night_total_h = domain_window_hours(night_start, night_end)

    if not soldier_ids:
        return {}, {}

    all_ivs = (
        db.query(PresenceInterval)
        .filter(
            PresenceInterval.soldier_id.in_(soldier_ids),
            PresenceInterval.status == "PRESENT",
        )
        .all()
    )

    by_soldier: dict[int, list[PresenceInterval]] = {sid: [] for sid in soldier_ids}
    for iv in all_ivs:
        if iv.soldier_id in by_soldier:
            by_soldier[iv.soldier_id].append(iv)

    sol_day_pres: dict[int, float] = {}
    sol_night_pres: dict[int, float] = {}

    for sid in soldier_ids:
        day_pres = 0.0
        night_pres = 0.0
        for iv in by_soldier[sid]:
            # Walk through each calendar day the interval spans.
            cur = iv.start_time.date()
            end_date = iv.end_time.date()
            if iv.end_time.time() == time(0, 0):
                end_date -= timedelta(days=1)
            while cur <= end_date:
                (day_s, day_e), (night_s, night_e) = domain_windows(
                    cur, night_start, night_end
                )
                day_pres += _overlap_hours([iv], day_s, day_e)
                night_pres += _overlap_hours([iv], night_s, night_e)
                cur += timedelta(days=1)
        sol_day_pres[sid] = day_pres
        sol_night_pres[sid] = night_pres

    return sol_day_pres, sol_night_pres


def weighted_avg_sd(
    hours: dict[int, float],
    fracs: dict[int, float],
) -> tuple[float, float]:
    """Compute presence-weighted average and standard deviation.

    weighted_avg = Σ hours_i / Σ frac_i  (soldiers with frac > 0 only)
    weighted_sd  = sqrt(Σ(frac_i * (rate_i - avg)^2) / Σ frac_i)

    Returns (avg, sd).  Returns (0, 0) if no soldiers have presence.
    """
    import math

    total_hours = 0.0
    total_frac = 0.0
    rates: list[tuple[float, float]] = []  # (rate, frac)

    for sid, frac in fracs.items():
        if frac < 0.001:
            continue
        h = hours.get(sid, 0.0)
        total_hours += h
        total_frac += frac
        rates.append((h / frac, frac))

    if total_frac < 0.001:
        return 0.0, 0.0

    avg = total_hours / total_frac

    variance = sum(f * (r - avg) ** 2 for r, f in rates) / total_frac
    return avg, math.sqrt(variance)
