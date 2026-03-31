"""
Test 3 — Constrained-first slicing.

Verifies that a soldier whose presence only partially overlaps a fractionable
task window is assigned exclusively within their available window.
"""
from datetime import datetime

from src.core.models import Soldier, Task, TaskAssignment
from src.core.engine import TaskAllocator
from src.domain.presence_rules import insert_presence_interval


def _soldier(db, name, presence_start, presence_end, roles=None):
    s = Soldier(name=name, phone_number="000", role=roles or [], is_active_in_kav=True)
    db.add(s)
    db.flush()
    insert_presence_interval(db, s.id, presence_start, presence_end, "PRESENT")
    db.flush()
    return s


def test_partial_presence_stays_within_window(db):
    """
    Task runs 06:00–18:00 (12 h, fractionable, concurrent=1).
    Soldier A present all day (00:00–23:59).
    Soldier B present only 06:00–12:00 (6 h).

    Soldier B's assignments must all fall within 06:00–12:00.
    """
    task_start = datetime(2026, 3, 10, 6, 0)
    task_end = datetime(2026, 3, 10, 18, 0)

    full = _soldier(db, "Alpha",
                    datetime(2026, 3, 10, 0, 0),
                    datetime(2026, 3, 10, 23, 59))
    partial = _soldier(db, "Bravo",
                       datetime(2026, 3, 10, 6, 0),
                       datetime(2026, 3, 10, 12, 0))

    task = Task(
        real_title="Long patrol",
        start_time=task_start,
        end_time=task_end,
        is_fractionable=True,
        required_count=1,
        required_roles_list=[],
        is_active=True,
    )
    db.add(task)
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    partial_assignments = (
        db.query(TaskAssignment)
        .filter(
            TaskAssignment.task_id == task.id,
            TaskAssignment.soldier_id == partial.id,
        )
        .all()
    )

    for a in partial_assignments:
        assert a.start_time >= datetime(2026, 3, 10, 6, 0), (
            f"Partial soldier assigned before their presence starts: {a.start_time}"
        )
        assert a.end_time <= datetime(2026, 3, 10, 12, 0), (
            f"Partial soldier assigned after their presence ends: {a.end_time}"
        )
