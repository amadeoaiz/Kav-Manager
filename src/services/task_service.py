from __future__ import annotations

from datetime import datetime, time, timedelta, date as date_type

from sqlalchemy.orm import Session

from src.core.models import (
    Task, TaskAssignment, MissionRequirement,
    Soldier, PresenceInterval,
)
from src.domain.task_rules import _task_roles_list


class TaskService:
    """
    Application service for Task and MissionRequirement CRUD.
    Wraps all Task and MissionRequirement DB access so the UI layer
    and bot never query these models directly.
    """

    def __init__(self, db: Session):
        self.db = db

    # ── Task CRUD ──────────────────────────────────────────────────────────

    def list_tasks(self, active_only: bool = True) -> list[Task]:
        q = self.db.query(Task)
        if active_only:
            q = q.filter(Task.is_active == True)
        return q.order_by(Task.start_time).all()

    def get_task(self, task_id: int) -> Task | None:
        return self.db.query(Task).filter(Task.id == task_id).first()

    def create_task(
        self,
        real_title: str,
        start_time: datetime,
        end_time: datetime,
        is_fractionable: bool = True,
        required_count: int = 1,
        required_roles_list: list | dict | None = None,
        base_weight: float = 1.0,
        readiness_minutes: int = 0,
        hardness: int = 3,
        excluded_soldier_ids: list | None = None,
        include_commander: bool = False,
        coverage_status: str = 'UNCOVERED',
    ) -> Task:
        """
        Creates a task with full validation.
        Raises ValueError on invalid input or if no eligible soldier is available.
        """
        if start_time >= end_time:
            raise ValueError(
                f"Task '{real_title}': start_time must be before end_time "
                f"(got {start_time} → {end_time})."
            )

        roles = required_roles_list or []
        if required_count < 1 and not roles:
            raise ValueError(
                f"Task '{real_title}': required_count must be >= 1 "
                f"or required_roles_list must be non-empty."
            )

        task = Task(
            real_title=real_title,
            start_time=start_time,
            end_time=end_time,
            is_fractionable=is_fractionable,
            required_count=required_count,
            required_roles_list=roles,
            base_weight=base_weight,
            readiness_minutes=readiness_minutes,
            hardness=hardness,
            excluded_soldier_ids=excluded_soldier_ids,
            include_commander=include_commander,
            coverage_status=coverage_status,
        )
        self.db.add(task)
        self.db.flush()

        coverage_warning = self._check_prospective_coverage(task)

        self.db.commit()

        if coverage_warning:
            print(f"Warning: {coverage_warning}")

        return task

    def save_task(self, task: Task) -> Task:
        """Add a new task (no validation) and commit. Used by dialogs that build the Task themselves."""
        self.db.add(task)
        self.db.commit()
        return task

    def update_task(self, task_id: int, **fields) -> Task | None:
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return None
        for key, value in fields.items():
            setattr(task, key, value)
        self.db.commit()
        return task

    def commit_task(self, task: Task) -> None:
        """Commit pending changes on an already-loaded task object."""
        self.db.commit()

    def expire_task(self, task: Task) -> None:
        """Expire a task so the next access reloads from DB."""
        self.db.expire(task)

    def delete_task(self, task_id: int) -> bool:
        """Deletes a task and all its assignments. Returns False if not found."""
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return False
        self.db.query(TaskAssignment).filter(
            TaskAssignment.task_id == task_id
        ).delete()
        self.db.delete(task)
        self.db.commit()
        return True

    def deactivate_task(self, task_id: int) -> bool:
        """Sets is_active=False and commits. Returns False if not found."""
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return False
        task.is_active = False
        self.db.commit()
        return True

    def set_include_commander(self, task_id: int, value: bool) -> None:
        """Set include_commander flag on a task (no commit — caller batches)."""
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if task:
            task.include_commander = value

    def commit(self) -> None:
        """Expose commit for batched operations (e.g. flip multiple flags then commit)."""
        self.db.commit()

    # ── Task queries ───────────────────────────────────────────────────────

    def get_uncovered_tasks(self) -> list[Task]:
        """Active future tasks whose coverage_status != 'OK'."""
        return (
            self.db.query(Task)
            .filter(
                Task.coverage_status != 'OK',
                Task.is_active == True,
                Task.end_time > datetime.now(),
            )
            .all()
        )

    def get_tasks_for_date(self, target_date: date_type) -> list[Task]:
        """Active tasks overlapping a given calendar date."""
        day_start = datetime.combine(target_date, time.min)
        day_end = day_start + timedelta(days=1)
        return (
            self.db.query(Task)
            .filter(
                Task.is_active == True,
                Task.start_time < day_end,
                Task.end_time > day_start,
            )
            .order_by(Task.start_time)
            .all()
        )

    def get_active_future_tasks(self) -> list[Task]:
        """Active tasks whose end_time is in the future."""
        return (
            self.db.query(Task)
            .filter(Task.is_active == True, Task.end_time > datetime.now())
            .order_by(Task.start_time)
            .all()
        )

    def get_task_assignments(self, task_id: int) -> list[TaskAssignment]:
        """Assignments for a specific task, ordered by start_time."""
        return (
            self.db.query(TaskAssignment)
            .filter(TaskAssignment.task_id == task_id)
            .order_by(TaskAssignment.start_time)
            .all()
        )

    def count_active_tasks(self) -> int:
        return self.db.query(Task).filter(Task.is_active == True).count()

    # ── MissionRequirement CRUD ────────────────────────────────────────────

    def list_requirements(self) -> list[MissionRequirement]:
        return (
            self.db.query(MissionRequirement)
            .order_by(MissionRequirement.date_from)
            .all()
        )

    def get_requirement(self, req_id: int) -> MissionRequirement | None:
        return (
            self.db.query(MissionRequirement)
            .filter(MissionRequirement.id == req_id)
            .first()
        )

    def day_has_requirements(self, d: date_type) -> bool:
        day_start = datetime.combine(d, time(0, 0, 0))
        day_end = datetime.combine(d, time(23, 59, 59))
        return (
            self.db.query(MissionRequirement)
            .filter(
                MissionRequirement.date_from <= day_end,
                MissionRequirement.date_to >= day_start,
            )
            .first()
            is not None
        )

    def save_requirement(self, req: MissionRequirement) -> MissionRequirement:
        """Add a new requirement and commit."""
        self.db.add(req)
        self.db.commit()
        return req

    def commit_requirement(self, req: MissionRequirement) -> None:
        """Commit pending changes on an already-loaded requirement."""
        self.db.commit()

    def expire_requirement(self, req: MissionRequirement) -> None:
        """Expire so next access reloads from DB."""
        self.db.expire(req)

    def delete_requirement(self, req_id: int) -> bool:
        req = self.db.query(MissionRequirement).filter(
            MissionRequirement.id == req_id
        ).first()
        if not req:
            return False
        self.db.delete(req)
        self.db.commit()
        return True

    def delete_requirement_obj(self, req: MissionRequirement) -> None:
        """Delete a requirement object directly and commit."""
        self.db.delete(req)
        self.db.commit()

    # ── Coverage check (absorbed from UnitManager) ─────────────────────────

    def _check_prospective_coverage(self, task: Task) -> str | None:
        """
        Checks whether enough eligible soldiers are expected to be available.
        Raises ValueError if zero eligible soldiers exist.
        Returns a warning string if fewer than required, None if OK.
        """
        roles_needed = _task_roles_list(task)
        required_count = len(roles_needed)

        all_soldiers = (
            self.db.query(Soldier)
            .filter(Soldier.is_active_in_kav == True)
            .all()
        )
        eligible = []
        for s in all_soldiers:
            role_ok = any(
                r == "Soldier" or r in (s.role or [])
                for r in roles_needed
            )
            if not role_ok:
                continue
            overlap = self.db.query(PresenceInterval).filter(
                PresenceInterval.soldier_id == s.id,
                PresenceInterval.status == 'PRESENT',
                PresenceInterval.start_time < task.end_time,
                PresenceInterval.end_time > task.start_time,
            ).first()
            if overlap:
                eligible.append(s)

        if len(eligible) == 0:
            raise ValueError(
                f"Task '{task.real_title}' cannot be created: no eligible soldier is "
                f"expected to be present during {task.start_time} → {task.end_time}."
            )

        if len(eligible) < required_count:
            return (
                f"Task '{task.real_title}': only {len(eligible)} of "
                f"{required_count} required soldiers are expected to be available."
            )

        return None
