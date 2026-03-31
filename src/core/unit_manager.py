from __future__ import annotations
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from src.core.models import (
    Soldier, PresenceInterval, Task, TaskAssignment,
    MissionRequirement, SoldierRequest,
)
from src.domain.presence_rules import is_full_day_present
from src.domain.task_rules import _task_roles_list
from src.services.config_service import ConfigService
from src.services.request_service import RequestService
from src.services.schedule_service import ScheduleService
from src.services.soldier_service import SoldierService
from src.services.task_service import TaskService
from datetime import datetime, time, timedelta, date as date_type
from typing import TYPE_CHECKING


# ── Leave solver data structures (used by UnitManager and the UI) ─────────────

@dataclass
class ReplacementSlot:
    """One replacement soldier covering a set of days."""
    soldier: "Soldier"
    days: list["date_type"]
    has_conflict: bool = False
    conflict_notes: list[str] = field(default_factory=list)


@dataclass
class LeaveSolution:
    """A single coverage plan (SINGLE or SPLIT)."""
    solution_type: str          # 'SINGLE' | 'SPLIT'
    replacements: list[ReplacementSlot]
    score: float                # lower = better (sum of planned present-days)
    notes: str


@dataclass
class LeaveSolverResult:
    """Full result returned by find_leave_solutions()."""
    soldier: "Soldier | None"
    leave_from: "date_type"
    leave_to: "date_type"
    critical_days: list["date_type"]
    already_short_days: list["date_type"]
    singles: list[LeaveSolution]
    splits: list[LeaveSolution]
    warnings: list[str]


class UnitManager:
    """Central API for unit logistics, presence, and task management.
    All UI and bot interactions must go through this class — never directly to the DB.
    """

    def __init__(self, db: Session):
        self.db = db
        self._config_svc = ConfigService(db)
        self._task_svc = TaskService(db)

    # =========================================================
    # 1. ARRIVALS AND DEPARTURES
    # =========================================================

    def register_arrival(self, soldier_id: int, arrival_date: date_type, duty_end_date: date_type) -> str:
        """
        Registers a soldier's arrival and creates a PRESENT interval from arrival_date
        at 12:00 until duty_end_date at 12:00.
        Normalizes points if the soldier has never been assigned a task (genuinely new).
        """
        return SoldierService(self.db).register_arrival(soldier_id, arrival_date, duty_end_date)

    def register_departure(self, soldier_id: int, departure_date: date_type, return_date: date_type,
                           departure_time: time = time(12, 0)) -> str:
        """
        Marks a soldier as on leave from departure_date at departure_time until
        return_date at 12:00. Both dates are required — no default duration.
        """
        return SoldierService(self.db).register_departure(
            soldier_id=soldier_id,
            departure_date=departure_date,
            return_date=return_date,
            departure_time=departure_time,
        )

    # =========================================================
    # 2. PRESENCE TIMELINE (internal)
    # =========================================================

    def _insert_presence(self, soldier_id: int, new_start: datetime, new_end: datetime, status: str) -> None:
        """
        Timeline-safe interval insertion. Clips or splits any overlapping intervals
        before inserting the new one, guaranteeing no overlaps in the timeline.
        """
        SoldierService(self.db).insert_presence(soldier_id, new_start, new_end, status)

    # =========================================================
    # 4. UI GRID CALCULATOR
    # =========================================================

    def get_daily_status_code(self, soldier_id: int, target_date: date_type) -> str:
        """Delegates to SoldierService.get_daily_status_code."""
        return SoldierService(self.db).get_daily_status_code(soldier_id, target_date)

    def generate_ui_grid(self, start_date: date_type, days: int = 7) -> dict:
        """Delegates to SoldierService.generate_ui_grid."""
        return SoldierService(self.db).generate_ui_grid(start_date, days)

    # =========================================================
    # 5. ROLES AND TASK REQUIREMENTS
    # =========================================================

    def update_soldier_roles(self, soldier_id: int, new_roles: list) -> bool:
        """Delegates to SoldierService.update_roles."""
        return SoldierService(self.db).update_roles(soldier_id, new_roles)

    def create_task(self, real_title: str, start_time: datetime, end_time: datetime,
                    is_fractionable: bool = True, required_count: int = 1,
                    required_roles_list: list = None, base_weight: float = 1.0,
                    readiness_minutes: int = 0) -> Task:
        """
        Creates a task with full validation. Raises ValueError on invalid input
        or if no eligible soldier is expected to be available.
        Delegates to TaskService.create_task.
        """
        return self._task_svc.create_task(
            real_title=real_title,
            start_time=start_time,
            end_time=end_time,
            is_fractionable=is_fractionable,
            required_count=required_count,
            required_roles_list=required_roles_list,
            base_weight=base_weight,
            readiness_minutes=readiness_minutes,
        )

    def update_mission_requirements(self, task_id: int, new_roles_list: list,
                                    new_weight: float = None) -> bool:
        """Updates a task's role requirements (writes to required_roles_list).
        Delegates to TaskService.update_task."""
        fields = {'required_roles_list': new_roles_list}
        if new_weight is not None:
            fields['base_weight'] = new_weight
        return self._task_svc.update_task(task_id, **fields) is not None

    # =========================================================
    # 6. COVERAGE CHECK (internal)
    # =========================================================

    def _check_prospective_coverage(self, task: Task) -> str | None:
        """Delegates to TaskService._check_prospective_coverage."""
        return self._task_svc._check_prospective_coverage(task)

    # =========================================================
    # 7. MANUAL SWAP
    # =========================================================

    def swap_assignment(self, assignment_id: int, new_soldier_id: int) -> str:
        """Delegates to ScheduleService.swap_assignment."""
        return ScheduleService(self.db).swap_assignment(assignment_id, new_soldier_id)

    # =========================================================
    # 8. UNPLANNED TASK REPORTING
    # =========================================================

    def report_unplanned_task(self, soldier_id: int, start_time: datetime,
                               end_time: datetime, description: str) -> TaskAssignment:
        """
        Soldier self-reports an unplanned task (e.g. via Matrix bot).
        - Flagged with pending_review=True for commander inspection.
        - Triggers reconcile (which calls resync_soldier_rates).
        """
        return RequestService(self.db).report_unplanned_task(
            soldier_id=soldier_id,
            start_time=start_time,
            end_time=end_time,
            description=description,
        )

    def review_unplanned_task(self, assignment_id: int, approved: bool) -> str:
        """
        Commander approves or rejects a self-reported unplanned task.
        - Approved: clears pending_review flag. Points already applied, no change.
        - Rejected: rolls back the soldier's points and marks the task inactive.
          Triggers reconcile so the schedule adjusts.
        """
        return RequestService(self.db).review_unplanned_task(
            assignment_id=assignment_id,
            approved=approved,
        )

    # =========================================================
    # 9. SERVICE COUNTER UPDATES
    # =========================================================

    def recalculate_service_counters(self, soldier_id: int) -> bool:
        """
        Recomputes present_days_count (float) and active_reserve_days (int)
        from the soldier's current presence intervals.
        """
        return SoldierService(self.db).recalculate_service_counters(soldier_id)

    # =========================================================
    # 10. LEAVE COVERAGE SOLVER
    # =========================================================

    def find_leave_solutions(
        self,
        soldier_id: int,
        leave_from: date_type,
        leave_to: date_type,
    ) -> "LeaveSolverResult":
        """
        Finds SINGLE and SPLIT coverage options for a soldier's leave request.

        SINGLE  — one replacement soldier covers all critical days.
        SPLIT   — two soldiers split the critical days; one comes back from home
                  earlier (covers the start of the period) and the other stays
                  at base later (covers the end of the period).

        Only soldiers who are currently ABSENT on the critical day(s) are
        candidates — having a present soldier counted is pointless.

        Soldiers are ranked by fewest total planned present-days in the period
        (most "slack" = fairest to ask). Soldiers with a LEAVE_REQUEST
        SoldierRequest on the critical day are excluded.

        Returns a LeaveSolverResult with lists of LeaveSolution objects and
        a diagnostic summary so the UI can show warnings.
        """
        soldier = self.db.query(Soldier).filter(Soldier.id == soldier_id).first()
        if not soldier:
            return LeaveSolverResult(
                soldier=None, leave_from=leave_from, leave_to=leave_to,
                critical_days=[], already_short_days=[],
                singles=[], splits=[], warnings=["Soldier not found."],
            )

        all_days = [
            leave_from + timedelta(days=i)
            for i in range((leave_to - leave_from).days + 1)
        ]

        critical_days: list[date_type] = []
        already_short: list[date_type] = []

        for d in all_days:
            status = self._day_readiness_status(d, exclude_soldier_id=soldier_id)
            if status == "short_with":
                already_short.append(d)
            elif status == "short_without":
                critical_days.append(d)
            # "ok_without" → non-critical, no action needed

        warnings: list[str] = []
        if already_short:
            day_strs = ", ".join(d.strftime("%d %b") for d in already_short)
            warnings.append(
                f"Unit is ALREADY SHORT on {day_strs} even with {soldier.name or f'#{soldier.id}'} present. "
                "These days cannot be fixed by this leave."
            )

        if not critical_days:
            return LeaveSolverResult(
                soldier=soldier, leave_from=leave_from, leave_to=leave_to,
                critical_days=[], already_short_days=already_short,
                singles=[], splits=[],
                warnings=warnings + [
                    "No critical days — the unit stays ready even without this soldier. "
                    "Leave can be approved freely."
                ],
            )

        # All active soldiers except the requesting one, sorted by planned present-days asc
        candidates = self._ranked_candidates(soldier_id, leave_from, leave_to)

        def _coverable_set(cand, days):
            """Returns (set_of_coverable_dates, combined_conflict_notes)."""
            flagged = self._coverable_days(cand, days)
            dates = {d for d, _ in flagged}
            notes = [n for _, n in flagged if n]
            return dates, notes

        # ── SINGLE solutions ──────────────────────────────────────────────────
        singles: list[LeaveSolution] = []
        for cand, present_days in candidates:
            coverable, conflicts = _coverable_set(cand, critical_days)
            if coverable >= set(critical_days):
                singles.append(LeaveSolution(
                    solution_type="SINGLE",
                    replacements=[ReplacementSlot(
                        soldier=cand,
                        days=critical_days,
                        has_conflict=bool(conflicts),
                        conflict_notes=conflicts,
                    )],
                    score=present_days,
                    notes=f"{cand.name or f'#{cand.id}'} covers all {len(critical_days)} "
                          f"critical day(s).  ({present_days} planned present-days this period)",
                ))
                if len(singles) >= 5:
                    break

        # ── SPLIT solutions ───────────────────────────────────────────────────
        splits: list[LeaveSolution] = []
        crit_sorted = sorted(critical_days)
        if len(crit_sorted) >= 2:
            for split_idx in range(1, len(crit_sorted)):
                early_days = crit_sorted[:split_idx]
                late_days  = crit_sorted[split_idx:]

                early_cands = [
                    (c, pd) for c, pd in candidates
                    if _coverable_set(c, early_days)[0] >= set(early_days)
                ]
                late_cands = [
                    (c, pd) for c, pd in candidates
                    if _coverable_set(c, late_days)[0] >= set(late_days)
                ]

                for (ca, pd_a) in early_cands[:3]:
                    for (cb, pd_b) in late_cands[:3]:
                        if ca.id == cb.id:
                            continue
                        pair_key = tuple(sorted([ca.id, cb.id]))
                        if any(
                            tuple(sorted([s.replacements[0].soldier.id,
                                          s.replacements[1].soldier.id])) == pair_key
                            for s in splits
                        ):
                            continue
                        _, ca_conflicts = _coverable_set(ca, early_days)
                        _, cb_conflicts = _coverable_set(cb, late_days)
                        splits.append(LeaveSolution(
                            solution_type="SPLIT",
                            replacements=[
                                ReplacementSlot(soldier=ca, days=early_days,
                                                has_conflict=bool(ca_conflicts),
                                                conflict_notes=ca_conflicts),
                                ReplacementSlot(soldier=cb, days=late_days,
                                                has_conflict=bool(cb_conflicts),
                                                conflict_notes=cb_conflicts),
                            ],
                            score=pd_a + pd_b,
                            notes=(
                                f"{ca.name or f'#{ca.id}'} covers "
                                f"{', '.join(d.strftime('%d %b') for d in early_days)};  "
                                f"{cb.name or f'#{cb.id}'} covers "
                                f"{', '.join(d.strftime('%d %b') for d in late_days)}."
                            ),
                        ))
                        if len(splits) >= 5:
                            break
                    if len(splits) >= 5:
                        break

        # Sort each list: best score first
        singles.sort(key=lambda s: s.score)
        splits.sort(key=lambda s: s.score)

        return LeaveSolverResult(
            soldier=soldier,
            leave_from=leave_from,
            leave_to=leave_to,
            critical_days=critical_days,
            already_short_days=already_short,
            singles=singles,
            splits=splits,
            warnings=warnings,
        )

    def apply_leave_solution(
        self,
        solution: "LeaveSolution",
        requesting_soldier_id: int,
        leave_from: date_type,
        leave_to: date_type,
    ) -> str:
        """
        Commits a chosen LeaveSolution to the database:
          1. Marks requesting soldier ABSENT for the full leave period.
          2. For each replacement, marks them PRESENT for their assigned days.
          3. Adds a SoldierRequest NOTE on each involved soldier.
        Returns a confirmation message.
        """
        req_soldier = self.db.query(Soldier).filter(
            Soldier.id == requesting_soldier_id
        ).first()
        if not req_soldier:
            return "Error: requesting soldier not found."

        # 1. Build day-level overrides for the requesting soldier:
        #    - leave_from .. leave_to      -> ABSENT days
        #    - leave_to + 1 (return day)   -> PRESENT day
        #    We then normalise the whole block so partial days appear only on
        #    transitions (P→A, A→P).
        return_day = leave_to + timedelta(days=1)
        window_start = leave_from - timedelta(days=1)
        window_end   = return_day + timedelta(days=1)
        overrides_req: dict[date_type, str] = {}
        d = leave_from
        while d <= leave_to:
            overrides_req[d] = "A"
            d += timedelta(days=1)
        overrides_req[return_day] = "P"

        self._normalize_presence_window(
            soldier_id=requesting_soldier_id,
            day_from=window_start,
            day_to=window_end,
            overrides=overrides_req,
            expand_same_state="A",
        )
        self.recalculate_service_counters(requesting_soldier_id)

        # 2. Mark each replacement present on their assigned critical days at the
        #    day level, then normalise so partials are derived from transitions.
        involved_names: list[str] = []
        for slot in solution.replacements:
            r = slot.soldier
            involved_names.append(r.name or f"#{r.id}")
            if not slot.days:
                continue

            days_sorted = sorted(slot.days)
            overrides_rep: dict[date_type, str] = {d: "P" for d in days_sorted}
            cov_start = days_sorted[0]
            cov_end   = days_sorted[-1]
            window_start = cov_start - timedelta(days=1)
            window_end   = cov_end + timedelta(days=1)
            self._normalize_presence_window(
                soldier_id=r.id,
                day_from=window_start,
                day_to=window_end,
                overrides=overrides_rep,
                expand_same_state="P",
            )
            self.recalculate_service_counters(r.id)

        # 3. Return a summary string; no automatic SoldierRequest notes are created.
        return (
            f"Applied. {req_soldier.name or f'#{req_soldier.id}'} is ABSENT "
            f"{leave_from.strftime('%d %b')}–{leave_to.strftime('%d %b')}. "
            f"Replacements: {', '.join(involved_names)}."
        )

    def _normalize_presence_window(
        self,
        soldier_id: int,
        day_from: date_type,
        day_to: date_type,
        overrides: dict[date_type, str] | None = None,
        expand_same_state: str | None = None,
    ) -> None:
        """Delegates to SoldierService.normalize_presence_window."""
        SoldierService(self.db).normalize_presence_window(
            soldier_id, day_from, day_to, overrides, expand_same_state,
        )

    # ── solver helpers ────────────────────────────────────────────────────────

    def _day_readiness_status(self, d: date_type, exclude_soldier_id: int) -> str:
        """
        Returns one of:
          'ok_without'     — unit is mission-ready even without this soldier
          'short_without'  — unit drops below ready without this soldier (critical day)
          'short_with'     — unit is already short even with this soldier (pre-existing gap)
        """
        day_start = datetime.combine(d, time(0, 0, 0))
        day_end   = datetime.combine(d, time(23, 59, 59))

        # Use the same notion of "present" as readiness_service.get_day_readiness:
        # only soldiers whose PRESENT intervals cover the full calendar day.
        present_intervals = (
            self.db.query(PresenceInterval)
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

        def _present_soldiers(exclude_id: int | None) -> list[Soldier]:
            full_ids = [
                sid
                for sid, ivs in by_soldier.items()
                if (exclude_id is None or sid != exclude_id)
                and is_full_day_present(ivs, day_start, day_end)
            ]
            if not full_ids:
                return []
            return (
                self.db.query(Soldier)
                .filter(Soldier.is_active_in_kav == True, Soldier.id.in_(full_ids))
                .all()
            )

        # Mission requirements for this day
        reqs = self.db.query(MissionRequirement).filter(
            MissionRequirement.date_from <= day_end,
            MissionRequirement.date_to   >= day_start,
        ).all()
        min_required = max((r.min_soldiers or 0 for r in reqs), default=0)

        # If no requirements at all, the day is never critical
        if min_required == 0 and not any(r.required_roles for r in reqs):
            return "ok_without"

        # Merge role requirements (quantity-aware)
        role_reqs: dict[str, int] = {}
        for r in reqs:
            rd = r.required_roles or {}
            if isinstance(rd, list):
                rd = {rn: 1 for rn in rd}
            for rn, cnt in rd.items():
                if rn == "Soldier":
                    # Universal role is implicit and never blocks readiness.
                    continue
                role_reqs[rn] = max(role_reqs.get(rn, 0), int(cnt))

        soldiers_with = _present_soldiers(None)
        soldiers_without = _present_soldiers(exclude_soldier_id)

        def _meets_readiness(soldiers: list[Soldier]) -> bool:
            if len(soldiers) < min_required:
                return False
            for role_name, needed in role_reqs.items():
                qualified = sum(
                    1 for s in soldiers
                    if self._soldier_qualifies_for_required_role(s, role_name)
                )
                if qualified < needed:
                    return False
            return True

        if not _meets_readiness(soldiers_with):
            return "short_with"
        if not _meets_readiness(soldiers_without):
            return "short_without"
        return "ok_without"

    def _soldier_qualifies_for_required_role(self, soldier: Soldier, required_role: str) -> bool:
        """Delegates to SoldierService.soldier_qualifies_for_required_role."""
        return SoldierService(self.db).soldier_qualifies_for_required_role(soldier, required_role)

    def _role_inherits_from(self, role_name: str, ancestor_name: str) -> bool:
        """Delegates to SoldierService.role_inherits_from."""
        return SoldierService(self.db).role_inherits_from(role_name, ancestor_name)

    def _ranked_candidates(
        self,
        exclude_soldier_id: int,
        period_from: date_type,
        period_to: date_type,
    ) -> list[tuple["Soldier", int]]:
        """Delegates to SoldierService.ranked_candidates."""
        return SoldierService(self.db).ranked_candidates(
            exclude_soldier_id, period_from, period_to,
        )

    def _coverable_days(
        self,
        soldier: "Soldier",
        days: list[date_type],
    ) -> list[tuple[date_type, str | None]]:
        """Delegates to SoldierService.coverable_days."""
        return SoldierService(self.db).coverable_days(soldier, days)
