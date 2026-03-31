from __future__ import annotations

import re
from sqlalchemy.orm import Session

from src.core.models import TaskTemplate, Task


_TIME_RE = re.compile(r'^([01]\d|2[0-3]):(00|15|30|45)$')


class TemplateService:
    """Application service for TaskTemplate CRUD.
    All template DB access goes through this service."""

    def __init__(self, db: Session):
        self.db = db

    # ── Queries ────────────────────────────────────────────────────────────

    def list_templates(self) -> list[TaskTemplate]:
        return (
            self.db.query(TaskTemplate)
            .order_by(TaskTemplate.name)
            .all()
        )

    def get_template(self, template_id: int) -> TaskTemplate | None:
        return self.db.query(TaskTemplate).filter(TaskTemplate.id == template_id).first()

    # ── Create ─────────────────────────────────────────────────────────────

    def create_template(
        self,
        name: str,
        start_time_of_day: str,
        end_time_of_day: str,
        is_fractionable: bool = True,
        required_count: int = 1,
        required_roles_list: list | dict | None = None,
        hardness: int = 3,
    ) -> TaskTemplate:
        self._validate(name, start_time_of_day, end_time_of_day, hardness)
        crosses = self._compute_crosses_midnight(start_time_of_day, end_time_of_day)
        tpl = TaskTemplate(
            name=name.strip(),
            start_time_of_day=start_time_of_day,
            end_time_of_day=end_time_of_day,
            crosses_midnight=crosses,
            is_fractionable=is_fractionable,
            required_count=required_count,
            required_roles_list=required_roles_list or [],
            hardness=hardness,
        )
        self.db.add(tpl)
        self.db.commit()
        return tpl

    def create_template_from_task(self, task: Task) -> TaskTemplate:
        """Extract time-of-day from a Task's datetimes and create a template."""
        start_tod = task.start_time.strftime("%H:%M")
        end_tod = task.end_time.strftime("%H:%M")
        return self.create_template(
            name=task.real_title or "Unnamed",
            start_time_of_day=start_tod,
            end_time_of_day=end_tod,
            is_fractionable=bool(task.is_fractionable),
            required_count=task.required_count or 1,
            required_roles_list=task.required_roles_list or [],
            hardness=task.hardness or 3,
        )

    # ── Update ─────────────────────────────────────────────────────────────

    def update_template(self, template_id: int, **fields) -> TaskTemplate | None:
        tpl = self.get_template(template_id)
        if not tpl:
            return None

        name = fields.get('name', tpl.name)
        start = fields.get('start_time_of_day', tpl.start_time_of_day)
        end = fields.get('end_time_of_day', tpl.end_time_of_day)
        hardness = fields.get('hardness', tpl.hardness)
        self._validate(name, start, end, hardness)

        for key, value in fields.items():
            if key == 'crosses_midnight':
                continue  # auto-computed
            setattr(tpl, key, value)

        tpl.crosses_midnight = self._compute_crosses_midnight(
            tpl.start_time_of_day, tpl.end_time_of_day
        )
        self.db.commit()
        return tpl

    # ── Delete ─────────────────────────────────────────────────────────────

    def delete_template(self, template_id: int) -> bool:
        tpl = self.get_template(template_id)
        if not tpl:
            return False
        self.db.delete(tpl)
        self.db.commit()
        return True

    # ── Duplicate ──────────────────────────────────────────────────────────

    def duplicate_template(self, template_id: int) -> TaskTemplate | None:
        tpl = self.get_template(template_id)
        if not tpl:
            return None
        return self.create_template(
            name=f"Copy of {tpl.name}",
            start_time_of_day=tpl.start_time_of_day,
            end_time_of_day=tpl.end_time_of_day,
            is_fractionable=tpl.is_fractionable,
            required_count=tpl.required_count,
            required_roles_list=tpl.required_roles_list or [],
            hardness=tpl.hardness,
        )

    # ── Validation ─────────────────────────────────────────────────────────

    @staticmethod
    def _validate(name: str, start: str, end: str, hardness: int):
        if not name or not name.strip():
            raise ValueError("Template name cannot be empty.")
        if not _TIME_RE.match(start):
            raise ValueError(
                f"start_time_of_day must be HH:MM on 15-min grid, got '{start}'."
            )
        if not _TIME_RE.match(end):
            raise ValueError(
                f"end_time_of_day must be HH:MM on 15-min grid, got '{end}'."
            )
        if not 1 <= hardness <= 5:
            raise ValueError(f"hardness must be 1–5, got {hardness}.")

    @staticmethod
    def _compute_crosses_midnight(start: str, end: str) -> bool:
        sh, sm = map(int, start.split(':'))
        eh, em = map(int, end.split(':'))
        return (eh * 60 + em) <= (sh * 60 + sm)
