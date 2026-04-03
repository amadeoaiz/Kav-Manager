from datetime import datetime

from sqlalchemy.orm import Session

from src.core.models import Soldier, Task, TaskAssignment
from src.services.schedule_service import ScheduleService


class RequestService:
    """
    Application service for pending-review and unplanned-task request workflows.
    """

    def __init__(self, db: Session):
        self.db = db

    def report_unplanned_task(
        self,
        soldier_id: int,
        start_time: datetime,
        end_time: datetime,
        description: str,
    ) -> TaskAssignment:
        """
        Soldier self-reports an unplanned task (e.g. via Matrix bot).
        - Flagged with pending_review=True for commander inspection.
        - Triggers reconcile (which calls resync_soldier_rates) for future windows.
        """
        soldier = self.db.query(Soldier).filter(Soldier.id == soldier_id).first()
        if not soldier:
            raise ValueError("Soldier not found.")

        if start_time >= end_time:
            raise ValueError("start_time must be before end_time.")

        duration_hours = (end_time - start_time).total_seconds() / 3600.0
        points_earned = 1.0 * duration_hours  # base_weight=1.0 for unplanned tasks

        # Create a one-off Task record for this event
        unplanned_task = Task(
            real_title=f"[UNPLANNED] {description}",
            start_time=start_time,
            end_time=end_time,
            is_fractionable=False,
            required_count=1,
            required_roles_list=[],
            base_weight=1.0,
            is_active=False,  # not managed by reconcile
        )
        self.db.add(unplanned_task)
        self.db.flush()

        assignment = TaskAssignment(
            soldier_id=soldier_id,
            task_id=unplanned_task.id,
            start_time=start_time,
            end_time=end_time,
            final_weight_applied=points_earned,
            pending_review=True,
            is_pinned=True,
        )
        self.db.add(assignment)

        self.db.commit()
        ScheduleService(self.db).reconcile()

        print(f"Unplanned task reported by {soldier.name} — pending commander review.")
        return assignment

    def review_unplanned_task(self, assignment_id: int, approved: bool) -> str:
        """
        Commander approves or rejects a self-reported unplanned task.
        - Approved: clears pending_review flag.
        - Rejected: deletes assignment, marks the task inactive.
          Triggers reconcile (which calls resync_soldier_rates).
        """
        assignment = self.db.query(TaskAssignment).filter(
            TaskAssignment.id == assignment_id
        ).first()
        if not assignment:
            return "Error: assignment not found."

        soldier = self.db.query(Soldier).filter(Soldier.id == assignment.soldier_id).first()

        if approved:
            assignment.pending_review = False
            self.db.commit()
            return f"Approved: assignment for {soldier.name} confirmed."

        # Mark the task as inactive so reconcile ignores it
        task = self.db.query(Task).filter(Task.id == assignment.task_id).first()
        if task:
            task.is_active = False

        self.db.delete(assignment)
        self.db.commit()
        ScheduleService(self.db).reconcile()

        return f"Rejected: assignment removed for {soldier.name}, schedule updated."
