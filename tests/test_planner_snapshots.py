from datetime import datetime, timedelta

from src.core.engine import TaskAllocator
from src.core.models import Soldier, Task, TaskAssignment
from src.domain.presence_rules import insert_presence_interval


BASE_DAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def test_build_soldier_and_task_snapshots(db):
    """Smoke-test the snapshot helpers used by the planner."""
    s = Soldier(name="Alpha", phone_number="000", role=["Driver"], is_active_in_kav=True)
    db.add(s)
    db.flush()

    insert_presence_interval(db, s.id, BASE_DAY, BASE_DAY + timedelta(days=30), "PRESENT")
    db.flush()

    task = Task(
        real_title="Guard",
        start_time=BASE_DAY + timedelta(days=9, hours=8),
        end_time=BASE_DAY + timedelta(days=9, hours=10),
        is_fractionable=True,
        required_count=1,
        required_roles_list=["Soldier"],
        is_active=True,
    )
    db.add(task)
    db.commit()

    allocator = TaskAllocator(db)
    now = BASE_DAY + timedelta(days=5, hours=12)

    soldiers = db.query(Soldier).all()
    tasks = db.query(Task).all()

    soldier_states = allocator._build_soldier_states(now, soldiers)
    task_specs = allocator._build_task_specs(tasks)

    assert len(soldier_states) == 1
    assert soldier_states[0].id == s.id
    assert soldier_states[0].roles == ["Driver"]
    assert soldier_states[0].presence_intervals, "Presence intervals should not be empty"

    assert len(task_specs) == 1
    assert task_specs[0].id == task.id
    assert task_specs[0].concurrent_required == 1
    assert task_specs[0].required_roles == ["Soldier"]


def test_reconcile_does_not_double_count_rates(db):
    """
    Reconcile + resync can be run multiple times without changing persisted
    rate columns; rates are derived from past TaskAssignment rows and
    present days only.
    """
    from src.utils.maintenance import resync_soldier_rates

    s = Soldier(
        name="Alpha",
        phone_number="000",
        role=[],
        is_active_in_kav=True,
        total_day_points=0.0,
        total_night_points=0.0,
    )
    db.add(s)
    db.flush()

    insert_presence_interval(db, s.id, BASE_DAY, BASE_DAY + timedelta(days=30), "PRESENT")
    db.flush()

    task = Task(
        real_title="Day duty",
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

    # First reconcile creates future assignments.
    allocator.reconcile_future()
    resync_soldier_rates(db)
    db.commit()
    db.refresh(s)
    day_rate_after_first = s.total_day_points

    past_assignments = db.query(TaskAssignment).all()
    assert past_assignments, "Expected at least one TaskAssignment after first reconcile"

    # Second reconcile should not change persisted rates.
    allocator.reconcile_future()
    resync_soldier_rates(db)
    db.commit()
    db.refresh(s)

    assert abs(s.total_day_points - day_rate_after_first) < 0.001


def test_writer_trims_grid_blocks_to_task_window(db):
    """
    Grid-planned blocks may extend slightly beyond the nominal task window
    on the 5-minute grid; persisted TaskAssignment rows must be trimmed to
    lie within [task.start_time, task.end_time].
    """
    s = Soldier(name="Delta", phone_number="000", role=[], is_active_in_kav=True)
    db.add(s)
    db.flush()

    # Presence covering a wide night interval.
    from src.domain.presence_rules import insert_presence_interval

    start = BASE_DAY + timedelta(days=1, hours=23, minutes=7)
    end = start + timedelta(hours=3, minutes=46)  # intentionally off the 5-minute grid
    insert_presence_interval(db, s.id, start - timedelta(hours=1), end + timedelta(hours=1), "PRESENT")
    db.flush()

    task = Task(
        real_title="Trimmed night guard",
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

    assignments = db.query(TaskAssignment).filter(TaskAssignment.task_id == task.id).all()
    assert assignments, "Expected TaskAssignment rows for the trimmed guard task"

    # Every persisted assignment must lie within the task window.
    for ta in assignments:
        assert task.start_time <= ta.start_time
        assert ta.end_time <= task.end_time

