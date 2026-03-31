"""
Tests for manual assignment editing: pinning, swapping, removing,
clearing before reconcile, and hours accounting.
"""
import datetime as _dt
from unittest.mock import patch

from sqlalchemy import func

from src.core.engine import TaskAllocator, FrozenAssignment
from src.core.models import Soldier, Task, TaskAssignment
from src.domain.presence_rules import insert_presence_interval


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXED_NOW = _dt.datetime(2026, 3, 10, 10, 0, 0)


class _FakeDatetime(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW


def _reconcile(allocator: TaskAllocator) -> None:
    with patch("src.core.engine.datetime", _FakeDatetime):
        allocator.reconcile_future()


def _make_soldier(db, name: str, phone: str = "000",
                  roles: list | None = None) -> Soldier:
    s = Soldier(
        name=name, phone_number=phone,
        role=roles or [], is_active_in_kav=True,
    )
    db.add(s)
    db.flush()
    # Present for the entire test day + next day (night tasks cross midnight)
    insert_presence_interval(
        db, s.id,
        _dt.datetime(2026, 3, 10, 0, 0),
        _dt.datetime(2026, 3, 11, 23, 59),
        "PRESENT",
    )
    return s


def _make_task(db, start, end, required_count=1,
               fractionable=True) -> Task:
    t = Task(
        real_title="TestTask",
        start_time=start,
        end_time=end,
        is_fractionable=fractionable,
        required_count=required_count,
        required_roles_list=[],
        is_active=True,
        readiness_minutes=0,
    )
    db.add(t)
    db.flush()
    return t


def _add_assignment(db, task, soldier, start=None, end=None,
                    is_pinned=False) -> TaskAssignment:
    s = start or task.start_time
    e = end or task.end_time
    dur_h = (e - s).total_seconds() / 3600.0
    a = TaskAssignment(
        task_id=task.id,
        soldier_id=soldier.id,
        start_time=s,
        end_time=e,
        final_weight_applied=(task.base_weight or 1.0) * dur_h,
        is_pinned=is_pinned,
    )
    db.add(a)
    db.flush()
    return a


def _assignments_for(db, task_id: int) -> list[TaskAssignment]:
    return (
        db.query(TaskAssignment)
        .filter(TaskAssignment.task_id == task_id)
        .order_by(TaskAssignment.start_time)
        .all()
    )


def _soldier_hours(db, soldier_id: int) -> float:
    """Sum of hours from all TaskAssignment rows for this soldier."""
    total = 0.0
    for a in db.query(TaskAssignment).filter(
        TaskAssignment.soldier_id == soldier_id
    ).all():
        total += (a.end_time - a.start_time).total_seconds() / 3600.0
    return total


# ---------------------------------------------------------------------------
# Test 1 — Pinned assignment survives reconcile
# ---------------------------------------------------------------------------

def test_pinned_survives_reconcile(db):
    """
    A future assignment with is_pinned=True must not be deleted during
    reconcile. It should be passed to the LP as a frozen assignment.
    """
    soldiers = [_make_soldier(db, f"S{i}") for i in range(4)]

    # Task starts in the future (after FIXED_NOW=10:00)
    task = _make_task(
        db,
        start=_dt.datetime(2026, 3, 10, 12, 0),
        end=_dt.datetime(2026, 3, 10, 16, 0),
    )

    # Pin soldier 0 to the task
    pinned_asgn = _add_assignment(
        db, task, soldiers[0],
        start=_dt.datetime(2026, 3, 10, 12, 0),
        end=_dt.datetime(2026, 3, 10, 14, 0),
        is_pinned=True,
    )
    pinned_id = pinned_asgn.id
    db.commit()

    _reconcile(TaskAllocator(db))

    # The pinned assignment must survive
    surviving = db.query(TaskAssignment).filter(
        TaskAssignment.id == pinned_id
    ).first()
    assert surviving is not None, "Pinned assignment was deleted during reconcile"
    assert surviving.soldier_id == soldiers[0].id
    assert surviving.is_pinned is True


# ---------------------------------------------------------------------------
# Test 2 — Swap exchanges soldier_ids and sets both pinned
# ---------------------------------------------------------------------------

def test_swap_sets_pinned_and_exchanges(db):
    """
    Swapping two soldiers between assignments should exchange soldier_ids
    and mark both assignments as is_pinned=True.
    """
    s1 = _make_soldier(db, "Alpha")
    s2 = _make_soldier(db, "Bravo")

    task_a = _make_task(
        db,
        start=_dt.datetime(2026, 3, 10, 12, 0),
        end=_dt.datetime(2026, 3, 10, 14, 0),
    )
    task_b = _make_task(
        db,
        start=_dt.datetime(2026, 3, 10, 12, 0),
        end=_dt.datetime(2026, 3, 10, 14, 0),
    )

    asgn_a = _add_assignment(db, task_a, s1)
    asgn_b = _add_assignment(db, task_b, s2)
    db.commit()

    # Simulate the swap that BlockEditDialog performs:
    # Commander clicks CHANGE on asgn_a, picks s2 (who is on asgn_b).
    old_soldier_id = asgn_a.soldier_id
    asgn_a.soldier_id = s2.id
    asgn_a.is_pinned = True
    asgn_b.soldier_id = old_soldier_id
    asgn_b.is_pinned = True
    db.commit()

    # Verify swap
    asgn_a_fresh = db.query(TaskAssignment).filter(
        TaskAssignment.id == asgn_a.id
    ).first()
    asgn_b_fresh = db.query(TaskAssignment).filter(
        TaskAssignment.id == asgn_b.id
    ).first()

    assert asgn_a_fresh.soldier_id == s2.id
    assert asgn_b_fresh.soldier_id == s1.id
    assert asgn_a_fresh.is_pinned is True
    assert asgn_b_fresh.is_pinned is True


# ---------------------------------------------------------------------------
# Test 3 — Remove a soldier from a block deletes the assignment
# ---------------------------------------------------------------------------

def test_remove_deletes_assignment(db):
    """
    Removing a soldier from a block should delete the TaskAssignment row.
    """
    s1 = _make_soldier(db, "Alpha")
    s2 = _make_soldier(db, "Bravo")

    task = _make_task(
        db,
        start=_dt.datetime(2026, 3, 10, 12, 0),
        end=_dt.datetime(2026, 3, 10, 14, 0),
        required_count=2,
    )

    asgn1 = _add_assignment(db, task, s1)
    asgn2 = _add_assignment(db, task, s2)
    db.commit()

    assert len(_assignments_for(db, task.id)) == 2

    # Simulate remove (as BlockEditDialog does)
    db.delete(asgn1)
    db.commit()

    remaining = _assignments_for(db, task.id)
    assert len(remaining) == 1
    assert remaining[0].soldier_id == s2.id


# ---------------------------------------------------------------------------
# Test 4 — Clear pinned before reconcile makes them replannable
# ---------------------------------------------------------------------------

def test_clear_pinned_before_reconcile(db):
    """
    Clearing is_pinned on future assignments before reconcile means they
    will be deleted and replanned normally.
    """
    soldiers = [_make_soldier(db, f"S{i}") for i in range(4)]

    task = _make_task(
        db,
        start=_dt.datetime(2026, 3, 10, 12, 0),
        end=_dt.datetime(2026, 3, 10, 16, 0),
    )

    pinned_asgn = _add_assignment(
        db, task, soldiers[0],
        start=_dt.datetime(2026, 3, 10, 12, 0),
        end=_dt.datetime(2026, 3, 10, 14, 0),
        is_pinned=True,
    )
    pinned_id = pinned_asgn.id
    db.commit()

    # Clear all pins (as PinnedConfirmDialog CLEAR action does)
    db.query(TaskAssignment).filter(
        TaskAssignment.is_pinned == True,
        TaskAssignment.end_time > FIXED_NOW,
    ).update({TaskAssignment.is_pinned: False}, synchronize_session='fetch')
    db.commit()

    # Verify pin cleared
    cleared = db.query(TaskAssignment).filter(
        TaskAssignment.id == pinned_id
    ).first()
    assert cleared.is_pinned is False

    # Reconcile — the assignment should be replannable (may be deleted/replaced)
    _reconcile(TaskAllocator(db))

    # The old pinned row may or may not survive (LP replans freely).
    # But the key assertion is that the LP was free to replan.
    all_asgns = _assignments_for(db, task.id)
    # Task should have coverage (LP should have assigned someone)
    assert len(all_asgns) > 0, "Reconcile should have created assignments for the task"


# ---------------------------------------------------------------------------
# Test 5 — Hours accounting reflects swapped assignment ownership
# ---------------------------------------------------------------------------

def test_hours_reflect_swap(db):
    """
    After swapping soldier_ids on assignments, querying total hours per
    soldier must reflect the new ownership (no stale caching).
    """
    s1 = _make_soldier(db, "Alpha")
    s2 = _make_soldier(db, "Bravo")

    task = _make_task(
        db,
        start=_dt.datetime(2026, 3, 10, 12, 0),
        end=_dt.datetime(2026, 3, 10, 14, 0),
    )

    # s1 gets a 2-hour assignment
    asgn = _add_assignment(db, task, s1)
    db.commit()

    assert _soldier_hours(db, s1.id) == 2.0
    assert _soldier_hours(db, s2.id) == 0.0

    # Swap: s2 takes over s1's assignment
    asgn.soldier_id = s2.id
    asgn.is_pinned = True
    db.commit()

    # Expire cached state so SQLAlchemy re-reads
    db.expire_all()

    assert _soldier_hours(db, s1.id) == 0.0, \
        "Old soldier should have 0 hours after swap"
    assert _soldier_hours(db, s2.id) == 2.0, \
        "New soldier should have the swapped hours"
