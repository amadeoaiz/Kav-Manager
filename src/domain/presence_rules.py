from datetime import datetime
from typing import List

from sqlalchemy.orm import Session

from src.core.models import PresenceInterval


def is_full_day_present(
    intervals: List[PresenceInterval],
    day_start: datetime,
    day_end: datetime,
) -> bool:
    """Return True if PRESENT intervals cover the whole calendar day [day_start, day_end]."""
    if not intervals:
        return False
    spans = sorted(
        [(max(iv.start_time, day_start), min(iv.end_time, day_end)) for iv in intervals],
        key=lambda x: x[0],
    )
    current_start, current_end = spans[0]
    if current_start > day_start:
        return False
    for s, e in spans[1:]:
        if s > current_end:
            return False
        if e > current_end:
            current_end = e
    return current_end >= day_end


def insert_presence_interval(
    db: Session,
    soldier_id: int,
    new_start: datetime,
    new_end: datetime,
    status: str,
) -> None:
    """
    Timeline-safe interval insertion. Clips or splits any overlapping intervals
    before inserting the new one, guaranteeing no overlaps in the timeline.
    """
    overlapping = db.query(PresenceInterval).filter(
        PresenceInterval.soldier_id == soldier_id,
        PresenceInterval.start_time < new_end,
        PresenceInterval.end_time > new_start,
    ).all()

    for existing in overlapping:
        e_start = existing.start_time
        e_end = existing.end_time
        e_status = existing.status
        db.delete(existing)

        if e_start < new_start:
            db.add(
                PresenceInterval(
                    soldier_id=soldier_id,
                    start_time=e_start,
                    end_time=new_start,
                    status=e_status,
                )
            )

        if e_end > new_end:
            db.add(
                PresenceInterval(
                    soldier_id=soldier_id,
                    start_time=new_end,
                    end_time=e_end,
                    status=e_status,
                )
            )

    db.add(
        PresenceInterval(
            soldier_id=soldier_id,
            start_time=new_start,
            end_time=new_end,
            status=status,
        )
    )
