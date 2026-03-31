from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from src.core.engine import TaskAllocator
from src.core.models import Soldier, Task, TaskAssignment
from src.utils.maintenance import resync_soldier_rates


class ScheduleService:
    """
    Application service that centralizes schedule reconciliation policy
    and all TaskAssignment queries/mutations used by the UI and bot layers.
    """

    def __init__(self, db: Session):
        self.db = db

    # ── Reconcile ─────────────────────────────────────────────────────────────

    def reconcile(self) -> TaskAllocator:
        """Run reconcile and return the allocator so callers can read results
        (e.g. overloaded_soldiers).  Resyncs soldier rates after commit."""
        allocator = TaskAllocator(self.db)
        allocator.reconcile_future()
        resync_soldier_rates(self.db)
        self.db.commit()
        return allocator

    def freeze_point(self, now):
        """
        Expose freeze policy: freeze_point is always now (no buffer).
        """
        return now

    # ── Assignment queries ────────────────────────────────────────────────────

    def get_soldier_assignments(
        self, soldier_id: int,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[TaskAssignment]:
        """Assignments for a soldier, optionally filtered by time window."""
        q = (
            self.db.query(TaskAssignment)
            .filter(TaskAssignment.soldier_id == soldier_id)
        )
        if start:
            q = q.filter(TaskAssignment.end_time > start)
        if end:
            q = q.filter(TaskAssignment.start_time < end)
        return q.order_by(TaskAssignment.start_time).all()

    def get_soldier_upcoming_assignments(
        self, soldier_id: int, from_time: datetime,
    ) -> list[TaskAssignment]:
        """Future assignments for a soldier (active tasks only)."""
        return (
            self.db.query(TaskAssignment)
            .join(Task)
            .filter(
                TaskAssignment.soldier_id == soldier_id,
                Task.is_active == True,
                TaskAssignment.end_time > from_time,
            )
            .order_by(TaskAssignment.start_time)
            .all()
        )

    def get_assignments_for_soldiers(
        self, soldier_ids: list[int],
        start: datetime, end: datetime,
    ) -> list[TaskAssignment]:
        """Assignments for multiple soldiers within a time window."""
        if not soldier_ids:
            return []
        return (
            self.db.query(TaskAssignment)
            .filter(
                TaskAssignment.soldier_id.in_(soldier_ids),
                TaskAssignment.start_time < end,
                TaskAssignment.end_time > start,
            )
            .all()
        )

    def get_block_assignments(
        self, task_id: int,
        block_start: datetime, block_end: datetime,
    ) -> list[TaskAssignment]:
        """Assignments for a task that overlap a time block."""
        return (
            self.db.query(TaskAssignment)
            .filter(
                TaskAssignment.task_id == task_id,
                TaskAssignment.start_time < block_end,
                TaskAssignment.end_time > block_start,
            )
            .all()
        )

    def get_assigned_soldier_ids_in_window(
        self, start: datetime, end: datetime,
        exclude_assignment_id: int | None = None,
    ) -> set[int]:
        """Set of soldier IDs with assignments overlapping a time window."""
        q = self.db.query(TaskAssignment.soldier_id).filter(
            TaskAssignment.start_time < end,
            TaskAssignment.end_time > start,
        )
        if exclude_assignment_id is not None:
            q = q.filter(TaskAssignment.id != exclude_assignment_id)
        return {row[0] for row in q.all()}

    def get_overlapping_assignments(
        self, soldier_id: int,
        block_start: datetime, block_end: datetime,
        exclude_task_id: int | None = None,
    ) -> list[TaskAssignment]:
        """Find assignments for a soldier that overlap a time window."""
        q = self.db.query(TaskAssignment).filter(
            TaskAssignment.soldier_id == soldier_id,
            TaskAssignment.start_time < block_end,
            TaskAssignment.end_time > block_start,
        )
        if exclude_task_id is not None:
            q = q.filter(TaskAssignment.task_id != exclude_task_id)
        return q.all()

    def get_all_active_assignments(
        self, start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[TaskAssignment]:
        """All assignments for active soldiers, optionally filtered by time window."""
        q = (
            self.db.query(TaskAssignment)
            .join(Soldier, TaskAssignment.soldier_id == Soldier.id)
            .filter(Soldier.is_active_in_kav == True)
        )
        if start:
            q = q.filter(TaskAssignment.end_time > start)
        if end:
            q = q.filter(TaskAssignment.start_time < end)
        return q.all()

    def get_first_assignment(self, soldier_id: int) -> TaskAssignment | None:
        """Earliest assignment for a soldier (by start_time)."""
        return (
            self.db.query(TaskAssignment)
            .filter(TaskAssignment.soldier_id == soldier_id)
            .order_by(TaskAssignment.start_time)
            .first()
        )

    # ── Pending review ────────────────────────────────────────────────────────

    def get_pending_review_assignments(self) -> list[TaskAssignment]:
        """Assignments flagged for commander review."""
        return (
            self.db.query(TaskAssignment)
            .filter(TaskAssignment.pending_review == True)
            .order_by(TaskAssignment.start_time)
            .all()
        )

    def count_pending_review(self) -> int:
        """Count of assignments awaiting review (for inbox badge)."""
        return (
            self.db.query(TaskAssignment)
            .filter(TaskAssignment.pending_review == True)
            .count()
        )

    def get_soldier_pending_review(self, soldier_id: int) -> list[TaskAssignment]:
        """Pending review assignments for a specific soldier."""
        return (
            self.db.query(TaskAssignment)
            .filter(
                TaskAssignment.soldier_id == soldier_id,
                TaskAssignment.pending_review == True,
            )
            .order_by(TaskAssignment.start_time)
            .all()
        )

    def count_soldier_pending_review(self, soldier_id: int) -> int:
        """Count of pending review assignments for a soldier."""
        return (
            self.db.query(TaskAssignment)
            .filter(
                TaskAssignment.soldier_id == soldier_id,
                TaskAssignment.pending_review == True,
            )
            .count()
        )

    def get_assignment(self, assignment_id: int) -> TaskAssignment | None:
        """Single assignment by ID."""
        return (
            self.db.query(TaskAssignment)
            .filter(TaskAssignment.id == assignment_id)
            .first()
        )

    # ── Pinned assignments ────────────────────────────────────────────────────

    def count_future_pinned(self) -> int:
        """Count of pinned assignments with end_time in the future."""
        return (
            self.db.query(TaskAssignment)
            .filter(
                TaskAssignment.is_pinned == True,
                TaskAssignment.end_time > datetime.now(),
            )
            .count()
        )

    def clear_future_pinned(self) -> None:
        """Clear the pinned flag on all future pinned assignments."""
        self.db.query(TaskAssignment).filter(
            TaskAssignment.is_pinned == True,
            TaskAssignment.end_time > datetime.now(),
        ).update({TaskAssignment.is_pinned: False}, synchronize_session='fetch')
        self.db.flush()

    # ── Assignment mutations ──────────────────────────────────────────────────

    def swap_assignment(self, assignment_id: int, new_soldier_id: int) -> str:
        """
        Peer-to-peer swap: replaces the soldier on an existing assignment slot.
        Resyncs soldier rates after commit. Does NOT trigger reconcile_future.
        """
        assignment = self.db.query(TaskAssignment).filter(
            TaskAssignment.id == assignment_id
        ).first()
        if not assignment:
            return "Error: assignment not found."

        new_soldier = self.db.query(Soldier).filter(
            Soldier.id == new_soldier_id
        ).first()
        if not new_soldier:
            return "Error: soldier not found."

        assignment.soldier_id = new_soldier_id
        self.db.commit()

        resync_soldier_rates(self.db)
        self.db.commit()

        return (
            f"Swap complete: {new_soldier.name} is now assigned to slot "
            f"{assignment.start_time.strftime('%Y-%m-%d %H:%M')} – "
            f"{assignment.end_time.strftime('%H:%M')}."
        )

    def change_assignment_soldier(
        self, assignment: TaskAssignment, new_soldier_id: int,
        pin: bool = True,
    ) -> None:
        """Change the soldier on an assignment (used by block edit)."""
        assignment.soldier_id = new_soldier_id
        if pin:
            assignment.is_pinned = True
        self.db.flush()

    def swap_block_assignments(
        self, assignment: TaskAssignment, other: TaskAssignment,
    ) -> None:
        """Exchange soldiers between two assignments (block edit swap)."""
        old_soldier_id = assignment.soldier_id
        assignment.soldier_id = other.soldier_id
        assignment.is_pinned = True
        other.soldier_id = old_soldier_id
        other.is_pinned = True
        self.db.flush()

    def remove_assignment(self, assignment: TaskAssignment) -> None:
        """Delete an assignment (block edit remove)."""
        self.db.delete(assignment)
        self.db.flush()

    def add_assignment(
        self, task_id: int, soldier_id: int,
        start_time: datetime, end_time: datetime,
        weight: float, pin: bool = True,
    ) -> TaskAssignment:
        """Create a new assignment (block edit add)."""
        asgn = TaskAssignment(
            task_id=task_id,
            soldier_id=soldier_id,
            start_time=start_time,
            end_time=end_time,
            final_weight_applied=weight,
            is_pinned=pin,
        )
        self.db.add(asgn)
        self.db.flush()
        return asgn

    def commit_block_edits(self) -> None:
        """Commit pending block-edit changes and resync soldier rates."""
        self.db.commit()
        resync_soldier_rates(self.db)
        self.db.commit()
