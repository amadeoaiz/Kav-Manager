"""
Test 4 — Manual swap.

Verifies:
- swap_assignment changes soldier_id on the assignment.
- After swap, resync_soldier_rates is called and rate columns reflect the new assignment.
"""
from datetime import datetime

from src.core.models import Soldier, Task, TaskAssignment
from src.core.unit_manager import UnitManager
from src.domain.presence_rules import insert_presence_interval


def test_swap_changes_only_soldier_id(db):
    s1 = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True,
                 total_day_points=0.0, total_night_points=0.0)
    s2 = Soldier(name="Bravo", phone_number="001", role=[], is_active_in_kav=True,
                 total_day_points=0.0, total_night_points=0.0)
    db.add_all([s1, s2])
    db.flush()

    for s in (s1, s2):
        insert_presence_interval(
            db, s.id, datetime(2026, 3, 1), datetime(2026, 3, 31), "PRESENT",
        )
    db.flush()

    task = Task(
        real_title="Guard",
        start_time=datetime(2026, 3, 10, 8, 0),
        end_time=datetime(2026, 3, 10, 12, 0),
        is_fractionable=False,
        required_count=1,
        required_roles_list=[],
        is_active=True,
    )
    db.add(task)
    db.flush()

    assignment = TaskAssignment(
        soldier_id=s1.id, task_id=task.id,
        start_time=task.start_time, end_time=task.end_time,
        final_weight_applied=4.0,
    )
    db.add(assignment)
    db.commit()

    um = UnitManager(db)
    result = um.swap_assignment(assignment.id, s2.id)

    assert "Swap complete" in result

    db.refresh(assignment)
    db.refresh(s1)
    db.refresh(s2)

    assert assignment.soldier_id == s2.id, "Assignment should now belong to Bravo"

    # After swap + resync: Bravo has the assignment hours, Alpha has none.
    # Both have 30 present days, so rates are hours/30 relative to average.
    # s2 rate = 4h/30 = 0.133, avg = 0.133/2 = 0.067 => s2 = +0.067, s1 = -0.067
    assert s2.total_day_points > 0, "Bravo should have positive day rate (overloaded)"
    assert s1.total_day_points < 0, "Alpha should have negative day rate (underloaded)"
