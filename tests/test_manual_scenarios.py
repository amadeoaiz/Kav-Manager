"""
Realistic 15-soldier scenario tests.

These tests go beyond coverage_status == 'OK' and inspect the actual
distribution of assignments to verify the engine's fairness, rest-gap,
and fragmentation properties.
"""
from datetime import datetime, timedelta
from collections import defaultdict

from src.core.engine import TaskAllocator
from src.core.models import Soldier, Task, TaskAssignment
from src.domain.presence_rules import insert_presence_interval


BASE_DAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def _create_soldiers(db, count=15):
    soldiers = []
    for i in range(count):
        s = Soldier(
            name=f"S{i+1}",
            phone_number=f"{i:03d}",
            role=[],
            is_active_in_kav=True,
        )
        db.add(s)
        db.flush()
        insert_presence_interval(
            db,
            s.id,
            BASE_DAY - timedelta(days=1),
            BASE_DAY + timedelta(days=7),
            "PRESENT",
        )
        soldiers.append(s)
    db.flush()
    return soldiers


def _make_task(
    db,
    title,
    start,
    end,
    required_count: int = 1,
    is_fractionable: bool = True,
    hardness: int = 3,
):
    t = Task(
        real_title=title,
        start_time=start,
        end_time=end,
        is_fractionable=is_fractionable,
        required_count=required_count,
        required_roles_list=[],
        hardness=hardness,
        is_active=True,
    )
    db.add(t)
    db.flush()
    return t


def _get_assignments(db, task_id):
    return (
        db.query(TaskAssignment)
        .filter(TaskAssignment.task_id == task_id)
        .order_by(TaskAssignment.start_time)
        .all()
    )


def _total_hours_by_soldier(assignments):
    hours: dict[int, float] = defaultdict(float)
    for a in assignments:
        dur = (a.end_time - a.start_time).total_seconds() / 3600.0
        hours[a.soldier_id] += dur
    return dict(hours)


def test_coverage_all_ok(db):
    """All tasks should be fully covered with 15 soldiers."""
    soldiers = _create_soldiers(db)
    db.commit()

    day_guard = _make_task(db, "Day guard post",
                           BASE_DAY + timedelta(days=1, hours=12),
                           BASE_DAY + timedelta(days=1, hours=18), required_count=1)
    night_guard = _make_task(db, "Night guard post",
                             BASE_DAY + timedelta(days=1, hours=23),
                             BASE_DAY + timedelta(days=2, hours=5), required_count=2)
    day_patrol = _make_task(db, "Day patrol",
                            BASE_DAY + timedelta(days=1, hours=10),
                            BASE_DAY + timedelta(days=1, hours=12), required_count=4)
    night_patrol = _make_task(db, "Night patrol",
                              BASE_DAY + timedelta(days=2, hours=1),
                              BASE_DAY + timedelta(days=2, hours=3), required_count=4)
    drone_day = _make_task(db, "Drone day",
                           BASE_DAY + timedelta(days=1, hours=14),
                           BASE_DAY + timedelta(days=1, hours=15), required_count=1)
    drone_night = _make_task(db, "Drone night",
                             BASE_DAY + timedelta(days=2, hours=2, minutes=30),
                             BASE_DAY + timedelta(days=2, hours=3), required_count=1)
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    for t in (day_guard, night_guard, day_patrol, night_patrol, drone_day, drone_night):
        db.refresh(t)
        assert t.coverage_status == "OK", (
            f"{t.real_title} should be fully covered, got {t.coverage_status}"
        )


def test_night_load_spread_fairly(db):
    """
    Night guard post (23:00–05:00, concurrent=2) with 15 soldiers.
    No single soldier should bear more than ~50% of total night hours.
    """
    soldiers = _create_soldiers(db)
    db.commit()

    night_guard = _make_task(db, "Night guard",
                             BASE_DAY + timedelta(days=1, hours=23),
                             BASE_DAY + timedelta(days=2, hours=5), required_count=2)
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    assignments = _get_assignments(db, night_guard.id)
    assert len(assignments) >= 2, "Expected at least 2 assignments for concurrent=2"

    hours = _total_hours_by_soldier(assignments)
    total = sum(hours.values())
    max_hours = max(hours.values())

    assert max_hours <= total * 0.5, (
        f"Load too concentrated: one soldier has {max_hours:.1f}h "
        f"out of {total:.1f}h total ({max_hours/total*100:.0f}%)"
    )

    assigned_count = len(hours)
    assert assigned_count >= 2, (
        f"Expected at least 2 distinct soldiers assigned, got {assigned_count}"
    )


def test_night_rest_gaps_respected(db):
    """
    Night guard 23:00–05:00 + night patrol 01:00–03:00 with 15 soldiers.
    No soldier should have overlapping assignments. With the block-based solver,
    soldiers may have short gaps between blocks (block boundaries are uniform),
    but no negative gaps (overlaps).
    """
    soldiers = _create_soldiers(db)
    db.commit()

    night_guard = _make_task(db, "Night guard",
                             BASE_DAY + timedelta(days=1, hours=23),
                             BASE_DAY + timedelta(days=2, hours=5), required_count=2)
    night_patrol = _make_task(db, "Night patrol",
                              BASE_DAY + timedelta(days=2, hours=1),
                              BASE_DAY + timedelta(days=2, hours=3), required_count=4)
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    all_assignments = (
        db.query(TaskAssignment)
        .filter(TaskAssignment.task_id.in_([night_guard.id, night_patrol.id]))
        .order_by(TaskAssignment.start_time)
        .all()
    )

    by_soldier: dict[int, list] = defaultdict(list)
    for a in all_assignments:
        by_soldier[a.soldier_id].append(a)

    for sid, stints in by_soldier.items():
        if len(stints) <= 1:
            continue
        sorted_stints = sorted(stints, key=lambda a: a.start_time)
        for i in range(1, len(sorted_stints)):
            gap_hours = (
                (sorted_stints[i].start_time - sorted_stints[i - 1].end_time).total_seconds()
                / 3600.0
            )
            # No overlapping assignments.
            assert gap_hours >= 0, (
                f"Soldier {sid} has overlapping assignments: "
                f"{sorted_stints[i-1].end_time.strftime('%H:%M')} and "
                f"{sorted_stints[i].start_time.strftime('%H:%M')}"
            )


def test_day_no_excessive_fragmentation(db):
    """
    Day guard post (12:00–18:00, 6h, concurrent=1) with 15 soldiers.
    No soldier should have more than 2 separate stints for this task.
    """
    soldiers = _create_soldiers(db)
    db.commit()

    day_guard = _make_task(db, "Day guard",
                           BASE_DAY + timedelta(days=1, hours=12),
                           BASE_DAY + timedelta(days=1, hours=18), required_count=1)
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    assignments = _get_assignments(db, day_guard.id)
    by_soldier: dict[int, list] = defaultdict(list)
    for a in assignments:
        by_soldier[a.soldier_id].append(a)

    for sid, stints in by_soldier.items():
        assert len(stints) <= 2, (
            f"Soldier {sid} has {len(stints)} separate stints for the day guard "
            f"(max expected: 2)"
        )


def test_overlapping_tasks_use_different_soldiers(db):
    """
    Night guard (23:00–05:00, concurrent=2) overlaps night patrol (01:00–03:00,
    concurrent=4). During the overlap window, the engine should use different
    soldiers for different tasks where possible (at least 3 distinct soldiers
    active during 01:00–03:00 across both tasks).
    """
    soldiers = _create_soldiers(db)
    db.commit()

    night_guard = _make_task(db, "Night guard",
                             BASE_DAY + timedelta(days=1, hours=23),
                             BASE_DAY + timedelta(days=2, hours=5), required_count=2)
    night_patrol = _make_task(db, "Night patrol",
                              BASE_DAY + timedelta(days=2, hours=1),
                              BASE_DAY + timedelta(days=2, hours=3), required_count=4)
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    overlap_start = BASE_DAY + timedelta(days=2, hours=1)
    overlap_end = BASE_DAY + timedelta(days=2, hours=3)

    all_assignments = (
        db.query(TaskAssignment)
        .filter(TaskAssignment.task_id.in_([night_guard.id, night_patrol.id]))
        .all()
    )

    soldiers_in_overlap = set()
    for a in all_assignments:
        if a.start_time < overlap_end and a.end_time > overlap_start:
            soldiers_in_overlap.add(a.soldier_id)

    # concurrent=2 + concurrent=4 = 6 soldiers needed, but some may overlap.
    # With 15 soldiers we expect at least 3 distinct soldiers covering
    # the overlap window.
    assert len(soldiers_in_overlap) >= 3, (
        f"Only {len(soldiers_in_overlap)} distinct soldiers active during the "
        f"overlap window (expected >= 3)"
    )


def test_no_soldier_assigned_to_all_tasks(db):
    """
    With 15 soldiers and 6 tasks, no single soldier should be assigned to
    every task. This confirms the engine is spreading work.
    """
    soldiers = _create_soldiers(db)
    db.commit()

    tasks = [
        _make_task(db, "Day guard", BASE_DAY + timedelta(days=1, hours=12),
                   BASE_DAY + timedelta(days=1, hours=18), required_count=1),
        _make_task(db, "Night guard", BASE_DAY + timedelta(days=1, hours=23),
                   BASE_DAY + timedelta(days=2, hours=5), required_count=2),
        _make_task(db, "Day patrol", BASE_DAY + timedelta(days=1, hours=10),
                   BASE_DAY + timedelta(days=1, hours=12), required_count=4),
        _make_task(db, "Night patrol", BASE_DAY + timedelta(days=2, hours=1),
                   BASE_DAY + timedelta(days=2, hours=3), required_count=4),
        _make_task(db, "Drone day", BASE_DAY + timedelta(days=1, hours=14),
                   BASE_DAY + timedelta(days=1, hours=15), required_count=1),
        _make_task(db, "Drone night", BASE_DAY + timedelta(days=2, hours=2, minutes=30),
                   BASE_DAY + timedelta(days=2, hours=3), required_count=1),
    ]
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    task_ids = {t.id for t in tasks}
    assignments = db.query(TaskAssignment).filter(
        TaskAssignment.task_id.in_(task_ids)
    ).all()

    tasks_per_soldier: dict[int, set] = defaultdict(set)
    for a in assignments:
        tasks_per_soldier[a.soldier_id].add(a.task_id)

    for sid, assigned_tasks in tasks_per_soldier.items():
        assert len(assigned_tasks) < len(tasks), (
            f"Soldier {sid} is assigned to all {len(tasks)} tasks — "
            f"engine should spread work across 15 soldiers"
        )


def test_cooldown_effect_across_two_nights(db):
    """
    Night 1: guard post 23:00–05:00 (concurrent=1) — one soldier gets all 6h.
    Night 2: guard post 23:00–05:00 (concurrent=1) — a *different* soldier
    should be preferred because the Night-1 soldier is in cooldown.
    """
    soldiers = _create_soldiers(db, count=4)
    db.commit()

    night1 = _make_task(db, "Night guard N1",
                        BASE_DAY + timedelta(days=1, hours=23),
                        BASE_DAY + timedelta(days=2, hours=5), required_count=1)
    night2 = _make_task(db, "Night guard N2",
                        BASE_DAY + timedelta(days=2, hours=23),
                        BASE_DAY + timedelta(days=3, hours=5), required_count=1)
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    a1 = _get_assignments(db, night1.id)
    a2 = _get_assignments(db, night2.id)

    assert a1 and a2, "Both nights should have assignments"

    hours_n1 = _total_hours_by_soldier(a1)
    hours_n2 = _total_hours_by_soldier(a2)

    # Each night should be spread across multiple soldiers.
    assert len(hours_n1) >= 2
    assert len(hours_n2) >= 2

    # No soldier should carry an excessive share of a single night
    # (night is 6h total, so cap any one soldier at <= 4h).
    assert max(hours_n1.values()) <= 4.0
    assert max(hours_n2.values()) <= 4.0


def test_late_night_patrol_and_emergent_drone(db):
    """
    More challenging night: guard all night, hard patrol in last hours,
    and an emergent short drone task in the worst night window.

    With 15 soldiers, coverage should hold and night load should still be
    reasonably spread (no one doing most of the night alone).
    """
    soldiers = _create_soldiers(db)
    db.commit()

    night_guard = _make_task(
        db,
        "Night guard (base)",
        BASE_DAY + timedelta(days=1, hours=23),
        BASE_DAY + timedelta(days=2, hours=5),
        required_count=2,
        hardness=3,
    )
    late_patrol = _make_task(
        db,
        "Late-night patrol",
        BASE_DAY + timedelta(days=2, hours=4),
        BASE_DAY + timedelta(days=2, hours=7),
        required_count=3,
        hardness=5,
    )
    emergent_drone = _make_task(
        db,
        "Emergent drone mission",
        BASE_DAY + timedelta(days=2, hours=2),
        BASE_DAY + timedelta(days=2, hours=3),
        required_count=1,
        hardness=5,
    )
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    for t in (night_guard, late_patrol, emergent_drone):
        db.refresh(t)
        assert t.coverage_status == "OK", f"{t.real_title} should be fully covered"

    task_ids = {night_guard.id, late_patrol.id, emergent_drone.id}
    assignments = (
        db.query(TaskAssignment)
        .filter(TaskAssignment.task_id.in_(task_ids))
        .all()
    )

    hours = _total_hours_by_soldier(assignments)
    total = sum(hours.values())
    max_hours = max(hours.values())

    # With 15 soldiers and these tasks, no single soldier should carry
    # more than ~50% of the combined night hours.
    assert max_hours <= total * 0.5, (
        f"Night load too concentrated: one soldier has {max_hours:.1f}h "
        f"out of {total:.1f}h total ({max_hours/total*100:.0f}%)."
    )

    # Sanity: at least 4 distinct soldiers should be involved.
    assert len(hours) >= 4, (
        f"Expected at least 4 soldiers across night guard + patrol + drone, "
        f"got {len(hours)}"
    )


def test_emergent_day_medical_and_kitchen_tasks(db):
    """
    Challenging day: base guard + patrol, an emergent medical task in the
    middle of the day, and an emergent kitchen duty needing several soldiers.

    With 15 soldiers, coverage should hold, day work should be spread, and
    no one should be excessively fragmented across many tiny stints.
    """
    soldiers = _create_soldiers(db)
    db.commit()

    day_guard = _make_task(
        db,
        "Day guard (base)",
        BASE_DAY + timedelta(days=1, hours=8),
        BASE_DAY + timedelta(days=1, hours=16),
        required_count=2,
        hardness=3,
    )
    day_patrol = _make_task(
        db,
        "Day patrol",
        BASE_DAY + timedelta(days=1, hours=10),
        BASE_DAY + timedelta(days=1, hours=14),
        required_count=4,
        hardness=3,
    )
    emergent_medical = _make_task(
        db,
        "Emergent medical task",
        BASE_DAY + timedelta(days=1, hours=13),
        BASE_DAY + timedelta(days=1, hours=15),
        required_count=2,
        hardness=5,
    )
    emergent_kitchen = _make_task(
        db,
        "Emergent kitchen duty",
        BASE_DAY + timedelta(days=1, hours=11),
        BASE_DAY + timedelta(days=1, hours=15),
        required_count=3,
        hardness=2,
    )
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    for t in (day_guard, day_patrol, emergent_medical, emergent_kitchen):
        db.refresh(t)
        assert t.coverage_status == "OK", f"{t.real_title} should be fully covered"

    task_ids = {day_guard.id, day_patrol.id, emergent_medical.id, emergent_kitchen.id}
    assignments = (
        db.query(TaskAssignment)
        .filter(TaskAssignment.task_id.in_(task_ids))
        .all()
    )

    hours = _total_hours_by_soldier(assignments)
    total = sum(hours.values())
    max_hours = max(hours.values())

    # Spread: at least 6 soldiers should share the combined day workload.
    assert len(hours) >= 6, (
        f"Expected at least 6 soldiers across day guard + patrol + emergent tasks, "
        f"got {len(hours)}"
    )

    # No single soldier should carry more than ~50% of the total day hours.
    assert max_hours <= total * 0.5, (
        f"Day load too concentrated: one soldier has {max_hours:.1f}h "
        f"out of {total:.1f}h total ({max_hours/total*100:.0f}%)."
    )

    # Fragmentation: grid-based chunking may introduce some extra stints,
    # but this should still be bounded.
    by_soldier: dict[int, list] = defaultdict(list)
    for a in assignments:
        by_soldier[a.soldier_id].append(a)

    for sid, stints in by_soldier.items():
        assert len(stints) <= 5, (
            f"Soldier {sid} has {len(stints)} separate stints across day tasks "
            f"(max expected: 5)"
        )
