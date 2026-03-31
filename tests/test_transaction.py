"""
Test 7 — Transaction integrity.

Verifies that if reconcile_future encounters an error mid-run,
no partial state is committed to the database.
"""
from datetime import datetime
from unittest.mock import patch

from src.core.models import Soldier, Task, TaskAssignment
from src.core.engine import TaskAllocator
from src.domain.presence_rules import insert_presence_interval


def test_rollback_on_failure(db):
    """
    Set up a valid scenario, then patch _commit_assignment to raise after
    the first call. The DB should have no new assignments — the transaction
    should be rolled back.
    """
    s1 = Soldier(name="Alpha", phone_number="000", role=[], is_active_in_kav=True)
    s2 = Soldier(name="Bravo", phone_number="001", role=[], is_active_in_kav=True)
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
        required_count=2,
        required_roles_list=[],
        is_active=True,
    )
    db.add(task)
    db.commit()

    count_before = db.query(TaskAssignment).count()

    call_count = 0
    original_commit_assignment = TaskAllocator._commit_assignment

    def exploding_commit(self_alloc, soldier, task_obj, start, end, is_night):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise RuntimeError("Simulated mid-reconcile failure")
        return original_commit_assignment(self_alloc, soldier, task_obj, start, end, is_night)

    allocator = TaskAllocator(db)

    with patch.object(TaskAllocator, '_commit_assignment', exploding_commit):
        try:
            allocator.reconcile_future()
        except RuntimeError:
            db.rollback()

    count_after = db.query(TaskAssignment).count()
    assert count_after == count_before, (
        f"Expected {count_before} assignments after rollback, got {count_after}. "
        "Partial state was committed."
    )
