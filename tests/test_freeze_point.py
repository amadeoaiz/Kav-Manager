"""
Tests for the readiness-aware freeze policy.

Freeze policy (no splitting):
  - freeze_point = now
  - end_time <= now → historical, left alone
  - start_time < now → in-progress, freeze entire assignment
  - start_time >= now AND start - readiness < now → gearing up, freeze
  - start_time >= now AND start - readiness >= now → delete and replan

No assignments are ever split at the freeze boundary.  The planner sees
whole frozen assignments and plans around them.
"""
import datetime as _dt
from collections import defaultdict
from unittest.mock import patch

from src.core.engine import TaskAllocator
from src.core.models import Soldier, Task, TaskAssignment
from src.domain.presence_rules import insert_presence_interval


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Fixed "now" keeps the freeze_point deterministic regardless of wall time.
FIXED_NOW = _dt.datetime(2026, 3, 10, 10, 0, 0)


class _FakeDatetime(_dt.datetime):
    """Drop-in replacement that pins datetime.now() to FIXED_NOW."""

    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW


def _reconcile(allocator: TaskAllocator) -> None:
    """Run one reconcile with time frozen at FIXED_NOW."""
    with patch("src.core.engine.datetime", _FakeDatetime):
        allocator.reconcile_future()


def _assignments_for(db, task_id: int) -> list:
    return (
        db.query(TaskAssignment)
        .filter(TaskAssignment.task_id == task_id)
        .order_by(TaskAssignment.soldier_id, TaskAssignment.start_time)
        .all()
    )


def _overlapping_pairs(assignments: list) -> list[tuple]:
    """Return list of (a, b) pairs where a and b overlap for the same soldier."""
    by_soldier: dict[int, list] = defaultdict(list)
    for a in assignments:
        by_soldier[a.soldier_id].append(a)

    pairs = []
    for rows in by_soldier.values():
        rows.sort(key=lambda a: a.start_time)
        for i in range(len(rows) - 1):
            if rows[i].end_time > rows[i + 1].start_time:
                pairs.append((rows[i], rows[i + 1]))
    return pairs


def _make_soldier(db, name: str, phone: str) -> Soldier:
    s = Soldier(name=name, phone_number=phone, role=[], is_active_in_kav=True)
    db.add(s)
    db.flush()
    insert_presence_interval(
        db,
        s.id,
        _dt.datetime(2026, 3, 10, 0, 0),
        _dt.datetime(2026, 3, 10, 23, 59),
        "PRESENT",
    )
    return s


def _make_task(db, start, end, readiness_minutes=0, required_count=1,
               fractionable=True) -> Task:
    t = Task(
        real_title="TestTask",
        start_time=start,
        end_time=end,
        is_fractionable=fractionable,
        required_count=required_count,
        required_roles_list=[],
        is_active=True,
        readiness_minutes=readiness_minutes,
    )
    db.add(t)
    db.flush()
    return t


def _add_assignment(db, task: Task, soldier: Soldier,
                    start=None, end=None) -> TaskAssignment:
    a = TaskAssignment(
        task_id=task.id,
        soldier_id=soldier.id,
        start_time=start or task.start_time,
        end_time=end or task.end_time,
    )
    db.add(a)
    return a


# ---------------------------------------------------------------------------
# Test 1 — In-progress assignment is frozen whole (not split)
# ---------------------------------------------------------------------------

def test_in_progress_assignment_frozen_whole(db):
    """
    A pre-existing assignment [09:00, 13:00] that is in-progress at now=10:00
    must be kept as a single whole row — never split at the freeze point.
    """
    s = _make_soldier(db, "Alpha", "001")
    task = _make_task(
        db,
        start=_dt.datetime(2026, 3, 10, 9, 0),
        end=_dt.datetime(2026, 3, 10, 13, 0),
    )
    _add_assignment(db, task, s)
    db.commit()

    _reconcile(TaskAllocator(db))

    rows = _assignments_for(db, task.id)
    # The original in-progress assignment should survive intact.
    in_progress = [
        r for r in rows
        if r.soldier_id == s.id
        and r.start_time == _dt.datetime(2026, 3, 10, 9, 0)
        and r.end_time == _dt.datetime(2026, 3, 10, 13, 0)
    ]
    assert len(in_progress) == 1, (
        f"Expected 1 whole in-progress assignment [09:00, 13:00], "
        f"found {len(in_progress)}. Rows: "
        + ", ".join(f"[{r.start_time}–{r.end_time}]" for r in rows)
    )
    assert not _overlapping_pairs(rows), "Overlapping assignments found"


# ---------------------------------------------------------------------------
# Test 2 — No overlapping rows after two successive reconciles
# ---------------------------------------------------------------------------

def test_no_overlapping_rows_after_two_reconciles(db):
    """
    Two successive reconciles must not produce overlapping or duplicate rows.
    """
    s1 = _make_soldier(db, "Alpha", "001")
    s2 = _make_soldier(db, "Bravo", "002")
    task = _make_task(
        db,
        start=_dt.datetime(2026, 3, 10, 9, 0),
        end=_dt.datetime(2026, 3, 10, 13, 0),
        required_count=2,
    )
    for s in (s1, s2):
        _add_assignment(db, task, s)
    db.commit()

    allocator = TaskAllocator(db)
    _reconcile(allocator)
    count_1 = db.query(TaskAssignment).filter(TaskAssignment.task_id == task.id).count()
    rows_1 = _assignments_for(db, task.id)
    assert not _overlapping_pairs(rows_1), "Overlapping rows after reconcile 1"

    _reconcile(allocator)
    count_2 = db.query(TaskAssignment).filter(TaskAssignment.task_id == task.id).count()
    rows_2 = _assignments_for(db, task.id)

    assert count_2 == count_1, (
        f"Row count changed: {count_1} after reconcile 1, {count_2} after reconcile 2"
    )
    assert not _overlapping_pairs(rows_2), "Overlapping rows after reconcile 2"


# ---------------------------------------------------------------------------
# Test 3 — Assignment count stable across reconciles
# ---------------------------------------------------------------------------

def test_assignment_count_stable_across_reconciles(db):
    """
    Ghost-row accumulation is impossible when assignments are never split.
    Verify row count is stable.
    """
    s = _make_soldier(db, "Alpha", "001")
    task = _make_task(
        db,
        start=_dt.datetime(2026, 3, 10, 9, 0),
        end=_dt.datetime(2026, 3, 10, 13, 0),
    )
    _add_assignment(db, task, s)
    db.commit()

    allocator = TaskAllocator(db)
    _reconcile(allocator)
    _reconcile(allocator)

    all_rows = _assignments_for(db, task.id)
    by_soldier: dict[int, list] = defaultdict(list)
    for a in all_rows:
        by_soldier[a.soldier_id].append(a.start_time)

    for sid, starts in by_soldier.items():
        duplicates = [t for t in starts if starts.count(t) > 1]
        assert not duplicates, (
            f"Soldier {sid} has duplicate start times: {list(set(duplicates))}"
        )

    assert not _overlapping_pairs(all_rows), "Overlapping assignments found"


# ---------------------------------------------------------------------------
# Test 4 — Gearing-up assignment is frozen
# ---------------------------------------------------------------------------

def test_gearing_up_assignment_frozen(db):
    """
    An assignment starting in 3 minutes with readiness_minutes=5 means the
    soldier started gearing up 2 minutes ago.  It must be frozen.
    """
    s = _make_soldier(db, "Alpha", "001")
    # Task starts at 10:03 (3 min from now=10:00), readiness=5min.
    # Gear-up started at 09:58, which is < now (10:00) → freeze.
    task = _make_task(
        db,
        start=_dt.datetime(2026, 3, 10, 10, 3),
        end=_dt.datetime(2026, 3, 10, 12, 0),
        readiness_minutes=5,
    )
    _add_assignment(db, task, s)
    db.commit()

    _reconcile(TaskAllocator(db))

    rows = _assignments_for(db, task.id)
    # The gearing-up assignment should survive as a whole row.
    frozen = [
        r for r in rows
        if r.soldier_id == s.id
        and r.start_time == _dt.datetime(2026, 3, 10, 10, 3)
        and r.end_time == _dt.datetime(2026, 3, 10, 12, 0)
    ]
    assert len(frozen) == 1, (
        f"Expected gearing-up assignment to be frozen whole. "
        f"Found rows: "
        + ", ".join(
            f"soldier={r.soldier_id} [{r.start_time}–{r.end_time}]"
            for r in rows
        )
    )


# ---------------------------------------------------------------------------
# Test 5 — Future assignment (not gearing up) is deleted and replanned
# ---------------------------------------------------------------------------

def test_future_assignment_replanned(db):
    """
    An assignment starting in 30 minutes with readiness_minutes=5 is NOT
    gearing up (start - readiness = +25min > now).  It should be deleted
    and replanned.
    """
    s = _make_soldier(db, "Alpha", "001")
    task = _make_task(
        db,
        start=_dt.datetime(2026, 3, 10, 10, 30),
        end=_dt.datetime(2026, 3, 10, 12, 0),
        readiness_minutes=5,
    )
    original_assignment = _add_assignment(db, task, s)
    original_id = original_assignment.id
    db.commit()

    _reconcile(TaskAllocator(db))

    # The original assignment should have been deleted (its ID gone).
    surviving = db.query(TaskAssignment).filter(
        TaskAssignment.id == original_id
    ).first()
    assert surviving is None, (
        "Future (non-gearing-up) assignment should have been deleted and replanned"
    )


# ---------------------------------------------------------------------------
# Test 6 — Historical assignment left untouched
# ---------------------------------------------------------------------------

def test_historical_assignment_untouched(db):
    """
    An assignment that ended before now must not be touched.
    """
    s = _make_soldier(db, "Alpha", "001")
    task = _make_task(
        db,
        start=_dt.datetime(2026, 3, 10, 7, 0),
        end=_dt.datetime(2026, 3, 10, 9, 0),
    )
    _add_assignment(db, task, s)
    db.commit()

    original_count = db.query(TaskAssignment).filter(
        TaskAssignment.task_id == task.id
    ).count()

    _reconcile(TaskAllocator(db))

    after_count = db.query(TaskAssignment).filter(
        TaskAssignment.task_id == task.id
    ).count()
    assert after_count == original_count, (
        f"Historical assignment was modified: {original_count} → {after_count}"
    )
