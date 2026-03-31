"""
Test 2 — Role matching in the LP solver.

Verifies:
- Task requiring "Soldier" (wildcard) accepts a Driver.
- Task requiring "Driver" rejects a plain Soldier.
- Task requiring "Driver" accepts an Operational Driver (child of Driver).

Dates are defined **relative to the current day** so that engine reconcile
always sees a future window.
"""
from datetime import datetime, timedelta

from src.core.models import Soldier, Task, TaskAssignment, Role
from src.core.engine import TaskAllocator, SoldierState, TaskSpec
from src.core.lp_solver import _build_eligible_matrix
from src.domain.presence_rules import insert_presence_interval


BASE_DAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
WINDOW_START = BASE_DAY + timedelta(days=9, hours=8)
WINDOW_END = BASE_DAY + timedelta(days=9, hours=12)
PRESENCE_START = BASE_DAY
PRESENCE_END = BASE_DAY + timedelta(days=30)


def _soldier(db, name, roles):
    s = Soldier(name=name, phone_number="000", role=roles, is_active_in_kav=True)
    db.add(s)
    db.flush()
    insert_presence_interval(db, s.id, PRESENCE_START, PRESENCE_END, "PRESENT")
    db.flush()
    return s


def _task(db, title, roles_list):
    t = Task(
        real_title=title,
        start_time=WINDOW_START,
        end_time=WINDOW_END,
        is_fractionable=False,
        required_count=1,
        required_roles_list=roles_list,
        is_active=True,
    )
    db.add(t)
    db.flush()
    return t


def _task_spec(roles):
    return TaskSpec(
        id=999,
        real_title="test",
        start_time=WINDOW_START,
        end_time=WINDOW_END,
        is_fractionable=False,
        is_night=False,
        required_roles=roles,
        concurrent_required=1,
        hardness=3,
        min_block_minutes=60,
        readiness_minutes=0,
    )


def test_wildcard_soldier_accepts_driver(db):
    """A task requiring 'Soldier' should accept any soldier, including a Driver."""
    driver = _soldier(db, "Alpha", ["Driver"])
    db.commit()

    # Use engine to expand roles (handles inheritance).
    allocator = TaskAllocator(db)
    states = allocator._build_soldier_states(datetime.now(), [driver])
    spec = _task_spec(["Soldier"])
    elig = _build_eligible_matrix(states, [spec])
    assert elig[driver.id][spec.id] is True


def test_driver_task_rejects_plain_soldier(db):
    """A task requiring 'Driver' should reject a plain soldier with no Driver role."""
    plain = _soldier(db, "Bravo", [])
    db.commit()

    allocator = TaskAllocator(db)
    states = allocator._build_soldier_states(datetime.now(), [plain])
    spec = _task_spec(["Driver"])
    elig = _build_eligible_matrix(states, [spec])
    assert elig[plain.id][spec.id] is False


def test_driver_task_accepts_operational_driver(db):
    """Operational Driver inherits from Driver, so it qualifies for a 'Driver' task."""
    # Set up role hierarchy in DB.
    driver_role = db.query(Role).filter(Role.name == "Driver").first()
    if not driver_role:
        driver_role = Role(name="Driver")
        db.add(driver_role)
        db.flush()
    op_role = db.query(Role).filter(Role.name == "Operational Driver").first()
    if not op_role:
        op_role = Role(name="Operational Driver", parent_role_id=driver_role.id)
        db.add(op_role)
        db.flush()

    op_driver = _soldier(db, "Charlie", ["Operational Driver"])
    db.commit()

    # Engine expands roles to include ancestors.
    allocator = TaskAllocator(db)
    states = allocator._build_soldier_states(datetime.now(), [op_driver])

    # Verify role expansion includes "Driver".
    state = states[0]
    assert "Driver" in state.roles, f"Expected 'Driver' in expanded roles, got {state.roles}"

    spec = _task_spec(["Driver"])
    elig = _build_eligible_matrix(states, [spec])
    assert elig[op_driver.id][spec.id] is True


def test_role_inheritance_engine_assignment(db):
    """
    Full integration: one task requiring 'Driver', two soldiers (plain + Driver).
    Only the Driver should be assigned.
    """
    # Ensure Driver role exists.
    driver_role = db.query(Role).filter(Role.name == "Driver").first()
    if not driver_role:
        driver_role = Role(name="Driver")
        db.add(driver_role)
        db.flush()

    plain = _soldier(db, "Alpha", [])
    driver = _soldier(db, "Bravo", ["Driver"])

    task = _task(db, "Driving patrol", ["Driver"])
    db.commit()

    allocator = TaskAllocator(db)
    allocator.reconcile_future()

    assignments = db.query(TaskAssignment).filter(TaskAssignment.task_id == task.id).all()
    assigned_ids = {a.soldier_id for a in assignments}

    assert driver.id in assigned_ids, "Driver should be assigned to a Driver-required task"
    assert plain.id not in assigned_ids, "Plain soldier should not be assigned to Driver task"
