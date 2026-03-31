"""
Reserve period resolution — shared by stats dialog, grid tab, and future stats tab.

Logic:
  1. Manual range: from UnitConfig.reserve_period_start / reserve_period_end.
  2. Auto-detect: earliest DRAFTED start, latest DRAFTED end across all soldiers.
     Falls back to earliest PRESENT start / latest PRESENT end if no drafts exist.
  3. Effective range: broader of the two (earlier start, later end).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.models import UnitConfig, DraftInterval, PresenceInterval


def resolve_reserve_period(db: Session) -> tuple[datetime | None, datetime | None]:
    """Return (start, end) of the effective reserve period, or (None, None)."""
    config = db.query(UnitConfig).first()

    # Manual range
    manual_start = config.reserve_period_start if config else None
    manual_end = config.reserve_period_end if config else None

    # Auto-detect from draft intervals
    auto_start, auto_end = _auto_detect_from_drafts(db)
    if auto_start is None:
        # Fallback to presence intervals
        auto_start, auto_end = _auto_detect_from_presence(db)

    # Merge: broader of the two
    starts = [d for d in (manual_start, auto_start) if d is not None]
    ends = [d for d in (manual_end, auto_end) if d is not None]

    if not starts and not ends:
        return None, None

    eff_start = min(starts) if starts else None
    eff_end = max(ends) if ends else None
    return eff_start, eff_end


def _auto_detect_from_drafts(db: Session) -> tuple[datetime | None, datetime | None]:
    row = (
        db.query(
            func.min(DraftInterval.start_time),
            func.max(DraftInterval.end_time),
        )
        .filter(DraftInterval.status == "DRAFTED")
        .first()
    )
    if row and row[0] is not None:
        return row[0], row[1]
    return None, None


def _auto_detect_from_presence(db: Session) -> tuple[datetime | None, datetime | None]:
    row = (
        db.query(
            func.min(PresenceInterval.start_time),
            func.max(PresenceInterval.end_time),
        )
        .filter(PresenceInterval.status == "PRESENT")
        .first()
    )
    if row and row[0] is not None:
        return row[0], row[1]
    return None, None
