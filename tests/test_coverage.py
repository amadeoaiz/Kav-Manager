"""
Test 6 — Coverage guarantee.

Verifies:
- Task creation fails with ValueError when no eligible soldiers exist.
- Task creation succeeds (with warning) when fewer than required soldiers exist.
- Engine sets coverage_status = 'UNCOVERED' when a task can't be fully covered at reconcile.

Dates are defined **relative to the current day** so tests remain stable
over time while still exercising reconcile_future on future windows.
"""
from datetime import datetime, timedelta

import pytest

from src.core.models import Soldier, Task, TaskAssignment
from src.core.unit_manager import UnitManager
from src.core.engine import TaskAllocator
from src.domain.presence_rules import insert_presence_interval


BASE_DAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
PRESENCE_START = BASE_DAY
PRESENCE_END = BASE_DAY + timedelta(days=30)
TASK_START = BASE_DAY + timedelta(days=9, hours=8)
TASK_END = BASE_DAY + timedelta(days=9, hours=12)


def _soldier(db, name, roles=None, present=True):
    s = Soldier(name=name, phone_number="000", role=roles or [], is_active_in_kav=True)
    db.add(s)
    db.flush()
    status = "PRESENT" if present else "ABSENT"
    insert_presence_interval(db, s.id, PRESENCE_START, PRESENCE_END, status)
    db.flush()
    return s


def test_create_task_no_eligible_soldiers_raises(db):
    """No soldiers present → ValueError."""
    _soldier(db, "Alpha", present=False)
    db.commit()

    um = UnitManager(db)
    with pytest.raises(ValueError, match="no eligible soldier"):
        um.create_task(
            real_title="Impossible task",
            start_time=TASK_START,
            end_time=TASK_END,
            is_fractionable=False,
            required_count=1,
        )


def test_create_task_fewer_than_required_succeeds_with_warning(db, capsys):
    """One soldier present but task needs 3 → task is created, warning is printed."""
    _soldier(db, "Alpha", present=True)
    db.commit()

    um = UnitManager(db)
    task = um.create_task(
        real_title="Understaffed",
        start_time=TASK_START,
        end_time=TASK_END,
        is_fractionable=False,
        required_count=3,
    )

    assert task is not None
    assert task.id is not None
    captured = capsys.readouterr()
    assert "only 1 of 3" in captured.out.lower() or "only 1" in captured.out


def test_engine_flags_uncovered_task(db):
    """
    Task requires Driver role, but only plain soldiers are present.
    Engine should set coverage_status = 'UNCOVERED'.
    """
    _soldier(db, "Alpha", roles=[], present=True)

    task = Task(
        real_title="Driver task",
        start_time=TASK_START,
        end_time=TASK_END,
        is_fractionable=False,
        required_count=1,
        required_roles_list=["Driver"],
        is_active=True,
    )
    db.add(task)
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    db.refresh(task)
    assert task.coverage_status == "UNCOVERED", (
        f"Expected UNCOVERED, got '{task.coverage_status}'"
    )

    assignments = db.query(TaskAssignment).filter(TaskAssignment.task_id == task.id).all()
    assert len(assignments) == 0
