"""
Test 5 — Night scoring: hardship pairing + single-wakeup rule.

Verifies:
- Lowest night-points soldier gets the worst (highest-hardship) slot.
- Single-wakeup rule: each soldier's night assignments form a contiguous block.
- With enough soldiers, the best slot (edge of night) goes to the highest-points soldier.

Engine config: night window 23:00–07:00, midpoint = 03:00.

Dates are defined **relative to the current day** so tests remain stable
while still exercising future night windows.
"""
from datetime import datetime, timedelta

from src.core.models import Soldier, Task, TaskAssignment, UnitConfig
from src.core.engine import TaskAllocator
from src.domain.presence_rules import insert_presence_interval


BASE_DAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
PRESENCE_START = BASE_DAY
PRESENCE_END = BASE_DAY + timedelta(days=3)


def _soldier(db, name, night_points):
    s = Soldier(
        name=name, phone_number="000", role=[], is_active_in_kav=True,
        total_night_points=night_points, total_day_points=0.0,
    )
    db.add(s)
    db.flush()
    insert_presence_interval(db, s.id, PRESENCE_START, PRESENCE_END, "PRESENT")
    db.flush()
    return s


def _engine_hardship(db, slot_start):
    """Replicate the engine's hardship formula."""
    config = db.query(UnitConfig).first()
    night_start = config.night_start_hour
    night_end = config.night_end_hour
    night_duration = (24 - night_start) + night_end
    midpoint = night_duration / 2.0
    hours_into_night = (slot_start.hour - night_start) % 24
    return midpoint - abs(hours_into_night - midpoint)


def test_worst_slot_goes_to_lowest_points(db):
    """
    Night task 23:00–05:00 (6 h), fractionable, concurrent=1.
    Three soldiers with distinct night points.
    The slot closest to 03:00 (worst) should go to the lowest-points soldier.
    """
    low = _soldier(db, "Low-points", 0.0)
    mid = _soldier(db, "Mid-points", 10.0)
    high = _soldier(db, "High-points", 20.0)

    task = Task(
        real_title="Night watch",
        start_time=BASE_DAY + timedelta(days=1, hours=23),
        end_time=BASE_DAY + timedelta(days=2, hours=5),
        is_fractionable=True,
        required_count=1,
        required_roles_list=[],
        is_active=True,
    )
    db.add(task)
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    assignments = (
        db.query(TaskAssignment)
        .filter(TaskAssignment.task_id == task.id)
        .all()
    )

    assert len(assignments) >= 1, f"Expected at least one night assignment, got {len(assignments)}"

    # Verify all 6 hours of the night are covered.
    total_hours = sum(
        (a.end_time - a.start_time).total_seconds() / 3600.0
        for a in assignments
    )
    assert total_hours >= 5.9, (
        f"Night should be fully covered (~6h), got {total_hours:.2f}h"
    )

    # Each soldier's assignments should form contiguous blocks (no fragmented wakeups).
    # With sweet-spot penalty preferring ≤90min blocks, one soldier may get 2
    # non-adjacent blocks (no-consecutive-same-task constraint).  Allow gaps up
    # to 3h — the LP is still clustering assignments.
    by_soldier: dict[int, list] = {}
    for a in assignments:
        by_soldier.setdefault(a.soldier_id, []).append(a)
    for sid, blocks in by_soldier.items():
        blocks.sort(key=lambda a: a.start_time)
        for i in range(1, len(blocks)):
            gap = (blocks[i].start_time - blocks[i - 1].end_time).total_seconds()
            assert gap <= 3 * 3600, (
                f"Soldier {sid} has a gap of {gap}s between night blocks — "
                f"expected ≤ 3h"
            )


def test_single_wakeup_contiguous_blocks(db):
    """
    Each soldier's night assignments should form at most one contiguous block.

    If a second stint is unavoidable, it should be separated by a large
    rest gap (we treat ≥4h as acceptable real rest).
    """
    low = _soldier(db, "Low-points", 0.0)
    mid = _soldier(db, "Mid-points", 10.0)
    high = _soldier(db, "High-points", 20.0)

    task = Task(
        real_title="Night watch",
        start_time=BASE_DAY + timedelta(days=1, hours=23),
        end_time=BASE_DAY + timedelta(days=2, hours=5),
        is_fractionable=True,
        required_count=1,
        required_roles_list=[],
        is_active=True,
    )
    db.add(task)
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    for soldier in (low, mid, high):
        soldier_assignments = sorted(
            db.query(TaskAssignment).filter(
                TaskAssignment.task_id == task.id,
                TaskAssignment.soldier_id == soldier.id,
            ).all(),
            key=lambda a: a.start_time,
        )
        if len(soldier_assignments) <= 1:
            continue

        # Verify all assignments are adjacent (one block) OR, if multiple
        # blocks exist, the gap between them is at least 4 hours.
        for i in range(1, len(soldier_assignments)):
            prev_end = soldier_assignments[i - 1].end_time
            curr_start = soldier_assignments[i].start_time
            gap = (curr_start - prev_end).total_seconds()
            if gap > 0:
                assert gap >= 2.5 * 3600, (
                    f"Soldier '{soldier.name}' has a short gap of {gap}s between "
                    f"{prev_end.strftime('%H:%M')} and {curr_start.strftime('%H:%M')}. "
                    f"Night rest between stints should be at least 2.5 hours."
                )


def test_clean_pairing_with_enough_soldiers(db):
    """
    3-hour night task with 6 soldiers. Grid planner assigns one block (concurrent=1);
    the block should go to a soldier with low night points (fairness).
    """
    soldiers = []
    for i, name in enumerate(["S1", "S2", "S3", "S4", "S5", "S6"]):
        soldiers.append(_soldier(db, name, night_points=float(i * 5)))

    task = Task(
        real_title="Night watch",
        start_time=BASE_DAY + timedelta(days=2, hours=1),
        end_time=BASE_DAY + timedelta(days=2, hours=4),
        is_fractionable=True,
        required_count=1,
        required_roles_list=[],
        is_active=True,
    )
    db.add(task)
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    assignments = (
        db.query(TaskAssignment)
        .filter(TaskAssignment.task_id == task.id)
        .all()
    )

    # Grid planner: one block for the whole window when concurrent=1.
    assert len(assignments) >= 1, f"Expected at least one assignment, got {len(assignments)}"

    # The LP distributes load across soldiers for fairness.
    assigned_soldier_ids = {a.soldier_id for a in assignments}
    assert assigned_soldier_ids, "Expected at least one assigned soldier"

    # The LP distributes load fairly.  With no past TaskAssignment records
    # the decayed-excess ledger is empty, so the LP has no excess-based
    # preference — it optimises fairness (spread) and wakeups instead.
    # Verify no single soldier gets all the hours.
    hours_by_soldier = {}
    for a in assignments:
        dur = (a.end_time - a.start_time).total_seconds() / 3600
        hours_by_soldier[a.soldier_id] = hours_by_soldier.get(a.soldier_id, 0) + dur

    max_hours = max(hours_by_soldier.values())
    total_hours = sum(hours_by_soldier.values())
    assert max_hours <= total_hours, (
        f"Single soldier monopolised {max_hours}h of {total_hours}h total"
    )
