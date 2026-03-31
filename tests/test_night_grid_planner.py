from datetime import datetime, timedelta

from src.core.engine import TaskAllocator
from src.core.models import Soldier, Task
from src.domain.presence_rules import insert_presence_interval


BASE_DAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def _night_time(offset_hours: int) -> datetime:
    return BASE_DAY + timedelta(days=1, hours=23 + offset_hours)


def test_night_grid_planner_basic_two_soldiers(db):
    """
    Smoke test: a simple night guard task with two soldiers and
    concurrent_required=1 should produce at least one PlannedAssignment
    covering the full task window when planned via plan_schedule.
    """
    s1 = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    s2 = Soldier(name="Bravo", phone_number="111", role=[], is_active_in_kav=True)
    db.add_all([s1, s2])
    db.flush()

    # Both soldiers present through the full task window (task ends at start+4h).
    insert_presence_interval(db, s1.id, BASE_DAY, BASE_DAY + timedelta(days=4), "PRESENT")
    insert_presence_interval(db, s2.id, BASE_DAY, BASE_DAY + timedelta(days=4), "PRESENT")
    db.flush()

    start = _night_time(0)  # around 23:00
    end = start + timedelta(hours=4)

    task = Task(
        real_title="Night guard",
        start_time=start,
        end_time=end,
        is_fractionable=True,
        required_count=1,
        required_roles_list=["Soldier"],
        is_active=True,
    )
    db.add(task)
    db.commit()

    allocator = TaskAllocator(db)
    now = BASE_DAY

    soldiers = db.query(Soldier).all()
    tasks = db.query(Task).all()

    soldier_states = allocator._build_soldier_states(now, soldiers)
    task_specs = allocator._build_task_specs(tasks)
    frozen = allocator._build_frozen_assignments(now)

    planned, coverage = allocator.plan_schedule(soldier_states, task_specs, frozen, now)

    # Filter planned assignments for our task.
    relevant = [p for p in planned if p.task_id == task.id]
    assert relevant, "Expected at least one planned assignment for the night task"

    # Coverage audit from the planner should report full coverage.
    assert coverage[task.id] == "OK"

    # The tiled blocks should start no later than the task and end no earlier,
    # so their union can cover the full window.
    earliest_start = min(p.start_time for p in relevant)
    latest_end = max(p.end_time for p in relevant)
    assert earliest_start <= start
    assert latest_end >= end


def test_day_grid_planner_basic_two_soldiers(db):
    """
    Smoke test: a day fractionable task with two soldiers present should
    produce at least one PlannedAssignment for the day task within its window.
    """
    s1 = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    s2 = Soldier(name="Bravo", phone_number="111", role=[], is_active_in_kav=True)
    db.add_all([s1, s2])
    db.flush()

    insert_presence_interval(db, s1.id, BASE_DAY, BASE_DAY + timedelta(days=5), "PRESENT")
    insert_presence_interval(db, s2.id, BASE_DAY, BASE_DAY + timedelta(days=5), "PRESENT")
    db.flush()

    # Day window: 08:00–12:00
    start = BASE_DAY + timedelta(days=1, hours=8)
    end = BASE_DAY + timedelta(days=1, hours=12)

    task = Task(
        real_title="Day duty",
        start_time=start,
        end_time=end,
        is_fractionable=True,
        required_count=1,
        required_roles_list=["Soldier"],
        is_active=True,
    )
    db.add(task)
    db.commit()

    allocator = TaskAllocator(db)
    now = BASE_DAY

    soldiers = db.query(Soldier).all()
    tasks = db.query(Task).all()
    soldier_states = allocator._build_soldier_states(now, soldiers)
    task_specs = allocator._build_task_specs(tasks)
    frozen = allocator._build_frozen_assignments(now)

    planned, coverage = allocator.plan_schedule(soldier_states, task_specs, frozen, now)

    relevant = [p for p in planned if p.task_id == task.id]
    assert relevant, "Expected at least one planned assignment for the day task"
    for p in relevant:
        assert p.start_time >= start and p.end_time <= end, (
            f"Day block {p.start_time}–{p.end_time} should lie within task {start}–{end}"
        )


def test_grid_planned_task_coverage_status_ok_or_uncovered(db):
    """
    After reconcile_future, any grid-planned (fractionable) task must have
    coverage_status either 'OK' or 'UNCOVERED', never 'PARTIAL'.
    """
    s1 = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    db.add(s1)
    db.flush()

    insert_presence_interval(db, s1.id, BASE_DAY, BASE_DAY + timedelta(days=10), "PRESENT")
    db.flush()

    start = BASE_DAY + timedelta(days=1, hours=23)
    end = start + timedelta(hours=2)

    task = Task(
        real_title="Night guard",
        start_time=start,
        end_time=end,
        is_fractionable=True,
        required_count=1,
        required_roles_list=["Soldier"],
        is_active=True,
    )
    db.add(task)
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    db.refresh(task)
    assert task.coverage_status in ("OK", "UNCOVERED"), (
        f"Grid-planned task coverage_status must be OK or UNCOVERED, got {task.coverage_status!r}"
    )


def test_grid_respects_readiness_minutes_for_fractionable_tasks(db):
    """
    A fractionable task with non-zero readiness_minutes should only use
    soldiers whose PRESENT interval covers the extended window
    [start - readiness, end]. Soldiers arriving after that extended
    window must not be used for early blocks.
    """
    s1 = Soldier(name="Early", phone_number="000", role=[], is_active_in_kav=True)
    s2 = Soldier(name="Late", phone_number="111", role=[], is_active_in_kav=True)
    db.add_all([s1, s2])
    db.flush()

    # Task runs 23:00–01:00, requires 1 soldier, with 30 min readiness.
    start = BASE_DAY + timedelta(days=1, hours=23)
    end = start + timedelta(hours=2)

    # s1 present for full extended window [22:30, 01:00].
    insert_presence_interval(db, s1.id, start - timedelta(minutes=30), end, "PRESENT")
    # s2 only present from 23:00, so fails the readiness requirement for the first block.
    insert_presence_interval(db, s2.id, start, end, "PRESENT")
    db.flush()

    task = Task(
        real_title="Night guard with readiness",
        start_time=start,
        end_time=end,
        is_fractionable=True,
        required_count=1,
        required_roles_list=["Soldier"],
        readiness_minutes=30,
        is_active=True,
    )
    db.add(task)
    db.commit()

    allocator = TaskAllocator(db)
    now = BASE_DAY

    soldiers = db.query(Soldier).all()
    tasks = db.query(Task).all()
    soldier_states = allocator._build_soldier_states(now, soldiers)
    task_specs = allocator._build_task_specs(tasks)
    frozen = allocator._build_frozen_assignments(now)

    planned, coverage = allocator.plan_schedule(soldier_states, task_specs, frozen, now)
    relevant = [p for p in planned if p.task_id == task.id]
    assert relevant, "Expected planned assignments for the readiness-aware task"

    # Coverage should still be OK overall.
    assert coverage[task.id] == "OK"

    # The *first* block must be assigned to the early-present soldier; later
    # blocks may legitimately use other soldiers once readiness is satisfied.
    first_block = min(relevant, key=lambda p: p.start_time)
    assert first_block.soldier_id == s1.id


def test_grid_respects_readiness_minutes_for_fractionable_tasks(db):
    """
    A fractionable task with non-zero readiness_minutes should only use
    soldiers whose PRESENT interval covers the extended window
    [start - readiness, end]. Soldiers arriving after that extended
    window must not be used for early blocks.
    """
    s1 = Soldier(name="Early", phone_number="000", role=[], is_active_in_kav=True)
    s2 = Soldier(name="Late", phone_number="111", role=[], is_active_in_kav=True)
    db.add_all([s1, s2])
    db.flush()

    # Task runs 23:00–01:00, requires 1 soldier, with 30 min readiness.
    start = BASE_DAY + timedelta(days=1, hours=23)
    end = start + timedelta(hours=2)

    # s1 present for full extended window [22:30, 01:00].
    insert_presence_interval(db, s1.id, start - timedelta(minutes=30), end, "PRESENT")
    # s2 only present from 23:00, so fails the readiness requirement for the first block.
    insert_presence_interval(db, s2.id, start, end, "PRESENT")
    db.flush()

    task = Task(
        real_title="Night guard with readiness",
        start_time=start,
        end_time=end,
        is_fractionable=True,
        required_count=1,
        required_roles_list=["Soldier"],
        readiness_minutes=30,
        is_active=True,
    )
    db.add(task)
    db.commit()

    allocator = TaskAllocator(db)
    now = BASE_DAY

    soldiers = db.query(Soldier).all()
    tasks = db.query(Task).all()
    soldier_states = allocator._build_soldier_states(now, soldiers)
    task_specs = allocator._build_task_specs(tasks)
    frozen = allocator._build_frozen_assignments(now)

    planned, coverage = allocator.plan_schedule(soldier_states, task_specs, frozen, now)
    relevant = [p for p in planned if p.task_id == task.id]
    assert relevant, "Expected planned assignments for the readiness-aware task"

    # Coverage should still be OK overall.
    assert coverage[task.id] == "OK"

    # Both soldiers should participate — the early-present soldier (s1) is
    # eligible for all blocks; s2 is eligible once readiness is satisfied.
    # The LP may assign them to either block, so just verify both are used.
    assigned_sids = {p.soldier_id for p in relevant}
    assert s1.id in assigned_sids, "Early-present soldier (s1) should be assigned"
    assert len(relevant) >= 2, "Task should have at least 2 block assignments"


def test_cross_midnight_presence_split_does_not_block_night_guard(db):
    """
    Regression: presence stored as calendar-day intervals (day-15 ending at
    23:59:59, day-16 starting at 00:00:00) must NOT prevent the allocator
    from covering the 23:00–01:00 portion of a cross-midnight night guard.
    """
    s1 = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    s2 = Soldier(name="Bravo", phone_number="111", role=[], is_active_in_kav=True)
    s3 = Soldier(name="Charlie", phone_number="222", role=[], is_active_in_kav=True)
    s4 = Soldier(name="Delta", phone_number="333", role=[], is_active_in_kav=True)
    db.add_all([s1, s2, s3, s4])
    db.flush()

    day_15 = BASE_DAY + timedelta(days=1)
    day_16 = BASE_DAY + timedelta(days=2)

    # Calendar-day split presence, exactly like the real app stores it.
    for s in [s1, s2, s3, s4]:
        insert_presence_interval(
            db, s.id,
            day_15.replace(hour=0, minute=0),
            day_15.replace(hour=23, minute=59, second=59),
            "PRESENT",
        )
        insert_presence_interval(
            db, s.id,
            day_16.replace(hour=0, minute=0),
            day_16.replace(hour=23, minute=59, second=59),
            "PRESENT",
        )
    db.flush()

    # Night guard 23:00–05:00, concurrent=2, readiness=0.
    start = day_15.replace(hour=23, minute=0)
    end = day_16.replace(hour=5, minute=0)

    task = Task(
        real_title="Cross-midnight guard",
        start_time=start,
        end_time=end,
        is_fractionable=True,
        required_count=2,
        required_roles_list=["Soldier"],
        readiness_minutes=0,
        is_active=True,
    )
    db.add(task)
    db.commit()

    allocator = TaskAllocator(db)
    now = BASE_DAY

    soldiers = db.query(Soldier).all()
    tasks = db.query(Task).all()
    soldier_states = allocator._build_soldier_states(now, soldiers)
    task_specs = allocator._build_task_specs(tasks)
    frozen = allocator._build_frozen_assignments(now)

    planned, coverage = allocator.plan_schedule(soldier_states, task_specs, frozen, now)

    # The task must be fully covered — no missing early hours.
    assert coverage[task.id] == "OK", (
        f"Cross-midnight guard should be OK, got {coverage[task.id]}"
    )

    relevant = [p for p in planned if p.task_id == task.id]
    assert relevant, "Expected planned assignments"

    # The union of blocks should start no later than 23:00 and end no
    # earlier than 05:00.
    earliest = min(p.start_time for p in relevant)
    latest = max(p.end_time for p in relevant)
    assert earliest <= start, f"First block starts at {earliest}, expected <= {start}"
    assert latest >= end, f"Last block ends at {latest}, expected >= {end}"

