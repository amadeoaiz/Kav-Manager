"""
Test 1 — Presence interval splitting and on-leave exclusion.

Verifies:
- ABSENT inserted into a PRESENT interval splits it correctly.
- On-leave (ABSENT) soldier is not scheduled by the engine.

Dates are defined **relative to the current day** for engine scheduling
tests so reconcile_future always sees future windows.
"""
from datetime import datetime, timedelta

from src.core.models import Soldier, PresenceInterval, Task
from src.core.engine import TaskAllocator
from src.domain.presence_rules import insert_presence_interval


BASE_DAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def _make_soldier(db, name="Alpha", roles=None):
    s = Soldier(name=name, phone_number="000", role=roles or [], is_active_in_kav=True)
    db.add(s)
    db.flush()
    return s


def test_absent_splits_present(db):
    """PRESENT [day1–day31] + ABSENT [day5–day10] → three intervals, no overlaps."""
    s = _make_soldier(db)

    day1 = BASE_DAY
    day31 = BASE_DAY + timedelta(days=30)
    insert_presence_interval(db, s.id, day1, day31, "PRESENT")
    db.flush()

    day5 = BASE_DAY + timedelta(days=4)
    day10 = BASE_DAY + timedelta(days=9)
    insert_presence_interval(db, s.id, day5, day10, "ABSENT")
    db.flush()

    intervals = (
        db.query(PresenceInterval)
        .filter(PresenceInterval.soldier_id == s.id)
        .order_by(PresenceInterval.start_time)
        .all()
    )

    assert len(intervals) == 3

    assert intervals[0].status == "PRESENT"
    assert intervals[0].start_time == day1
    assert intervals[0].end_time == day5

    assert intervals[1].status == "ABSENT"
    assert intervals[1].start_time == day5
    assert intervals[1].end_time == day10

    assert intervals[2].status == "PRESENT"
    assert intervals[2].start_time == day10
    assert intervals[2].end_time == day31


def test_absent_fully_inside_present(db):
    """ABSENT that fully covers PRESENT deletes it and replaces it."""
    s = _make_soldier(db)

    insert_presence_interval(
        db,
        s.id,
        BASE_DAY + timedelta(days=4),
        BASE_DAY + timedelta(days=9),
        "PRESENT",
    )
    db.flush()

    insert_presence_interval(
        db,
        s.id,
        BASE_DAY,
        BASE_DAY + timedelta(days=30),
        "ABSENT",
    )
    db.flush()

    intervals = (
        db.query(PresenceInterval)
        .filter(PresenceInterval.soldier_id == s.id)
        .all()
    )
    assert len(intervals) == 1
    assert intervals[0].status == "ABSENT"
    assert intervals[0].start_time == BASE_DAY
    assert intervals[0].end_time == BASE_DAY + timedelta(days=30)


def test_on_leave_soldier_not_scheduled(db):
    """A soldier with only an ABSENT interval covering the task window gets no assignments."""
    s = _make_soldier(db)
    insert_presence_interval(db, s.id, BASE_DAY, BASE_DAY + timedelta(days=30), "ABSENT")
    db.flush()

    # A second soldier who IS present — needed so the task can be created
    s2 = _make_soldier(db, name="Bravo")
    insert_presence_interval(db, s2.id, BASE_DAY, BASE_DAY + timedelta(days=30), "PRESENT")
    db.flush()

    task = Task(
        real_title="Guard",
        start_time=BASE_DAY + timedelta(days=9, hours=8),
        end_time=BASE_DAY + timedelta(days=9, hours=12),
        is_fractionable=False,
        required_count=1,
        required_roles_list=[],
        is_active=True,
    )
    db.add(task)
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    from src.core.models import TaskAssignment
    assignments = db.query(TaskAssignment).filter(TaskAssignment.task_id == task.id).all()

    assigned_ids = [a.soldier_id for a in assignments]
    assert s.id not in assigned_ids, "ABSENT soldier should not be assigned"
    assert s2.id in assigned_ids, "PRESENT soldier should be assigned"
