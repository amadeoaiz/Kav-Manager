from datetime import date, datetime, time

from sqlalchemy.orm import Session

from src.core.models import MissionRequirement, PresenceInterval, Soldier, Task, TaskAssignment
from src.domain.presence_rules import is_full_day_present


def get_day_requirements(db: Session, target_date: date) -> dict:
    """
    Return merged MissionRequirement data for a day (strictest rules win).
    required_roles is a dict {role_name: count}; counts are maximized across blocks.
    """
    day_start = datetime.combine(target_date, time(0, 0, 0))
    day_end = datetime.combine(target_date, time(23, 59, 59))
    reqs = db.query(MissionRequirement).filter(
        MissionRequirement.date_from <= day_end,
        MissionRequirement.date_to >= day_start,
    ).all()
    if not reqs:
        return {"min_soldiers": 0, "required_roles": {}, "labels": []}
    min_sol = max(r.min_soldiers or 0 for r in reqs)
    merged_roles: dict[str, int] = {}
    for r in reqs:
        role_data = r.required_roles or {}
        if isinstance(role_data, list):  # legacy list -> count=1 each
            role_data = {rn: 1 for rn in role_data}
        for role_name, count in role_data.items():
            merged_roles[role_name] = max(merged_roles.get(role_name, 0), int(count))
    labels = [r.label for r in reqs if r.label]
    return {"min_soldiers": min_sol, "required_roles": merged_roles, "labels": labels}


def get_day_readiness(db: Session, target_date: date) -> dict:
    """
    Compute readiness metrics for a calendar day, factoring in MissionRequirements.
    """
    day_start = datetime.combine(target_date, time(0, 0, 0))
    day_end = datetime.combine(target_date, time(23, 59, 59))

    tasks = db.query(Task).filter(
        Task.is_active == True,
        Task.start_time < day_end,
        Task.end_time > day_start,
    ).all()

    reqs = get_day_requirements(db, target_date)
    req_min = reqs["min_soldiers"]
    req_roles = reqs["required_roles"]  # dict {role_name: count}

    if not tasks and req_min == 0 and not req_roles:
        return {
            "status": "empty",
            "task_count": 0,
            "required": 0,
            "present": 0,
            "tasks": [],
            "requirements": reqs,
        }

    task_role_data = {}
    for t in tasks:
        trl = t.required_roles_list or {}
        if isinstance(trl, list):
            trl = {rn: 1 for rn in trl}
        for rn, cnt in trl.items():
            task_role_data[rn] = task_role_data.get(rn, 0) + int(cnt)
    task_soldier_req = sum(t.required_count or 1 for t in tasks if not t.required_roles_list)

    total_required = max(req_min, task_soldier_req)

    # Compute full-day PRESENT soldiers only — partial days (mixed presence/absence)
    # do not count as fully present for readiness headcount.
    present_intervals = (
        db.query(PresenceInterval)
        .filter(
            PresenceInterval.status == "PRESENT",
            PresenceInterval.start_time < day_end,
            PresenceInterval.end_time > day_start,
        )
        .all()
    )

    by_soldier: dict[int, list[PresenceInterval]] = {}
    for iv in present_intervals:
        by_soldier.setdefault(iv.soldier_id, []).append(iv)

    full_day_ids = [
        sid for sid, ivs in by_soldier.items()
        if is_full_day_present(ivs, day_start, day_end)
    ]

    if full_day_ids:
        present_soldiers = (
            db.query(Soldier)
            .filter(Soldier.is_active_in_kav == True, Soldier.id.in_(full_day_ids))
            .all()
        )
    else:
        present_soldiers = []

    present_count = len(present_soldiers)

    # Check required roles are covered (quantity-aware)
    roles_covered = True
    combined_role_reqs = {**task_role_data}
    for rn, cnt in req_roles.items():
        combined_role_reqs[rn] = max(combined_role_reqs.get(rn, 0), cnt)
    if combined_role_reqs:
        role_counts: dict[str, int] = {}
        for s in present_soldiers:
            for r in (s.role or []):
                role_counts[r] = role_counts.get(r, 0) + 1
        roles_covered = all(
            role_counts.get(rn, 0) >= cnt
            for rn, cnt in combined_role_reqs.items()
        )

    # Normalise legacy PARTIAL (no longer a valid final state) to UNCOVERED.
    statuses = [
        "UNCOVERED" if (t.coverage_status or "OK") == "PARTIAL" else (t.coverage_status or "OK")
        for t in tasks
    ]

    # Readiness legend semantics:
    #   surplus  — comfortably above requirements
    #   ok       — exactly at requirements
    #   partial  — below ideal but still meeting minimum headcount
    #   critical — mission readiness NOT met (uncovered tasks / missing roles / below minimum)
    if "UNCOVERED" in statuses or not roles_covered or present_count < req_min:
        status = "critical"
    elif present_count < total_required:
        status = "partial"
    elif present_count > total_required:
        status = "surplus"
    else:
        status = "ok"

    return {
        "status": status,
        "task_count": len(tasks),
        "required": total_required,
        "present": present_count,
        "tasks": tasks,
        "requirements": reqs,
    }


def get_day_schedule(db: Session, target_date: date) -> list[TaskAssignment]:
    """Return all assignments for a given day, sorted by start time."""
    day_start = datetime.combine(target_date, time(0, 0, 0))
    day_end = datetime.combine(target_date, time(23, 59, 59))
    return (
        db.query(TaskAssignment)
        .join(Task, Task.id == TaskAssignment.task_id)
        .filter(
            Task.is_active == True,
            TaskAssignment.start_time < day_end,
            TaskAssignment.end_time > day_start,
        )
        .order_by(TaskAssignment.start_time)
        .all()
    )


def get_active_now(db: Session) -> list[TaskAssignment]:
    now = datetime.now()
    return (
        db.query(TaskAssignment)
        .join(Task, Task.id == TaskAssignment.task_id)
        .filter(
            Task.is_active == True,
            TaskAssignment.start_time <= now,
            TaskAssignment.end_time > now,
        )
        .all()
    )
