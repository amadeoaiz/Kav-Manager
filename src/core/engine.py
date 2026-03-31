import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from src.core.models import Soldier, PresenceInterval, Task, TaskAssignment, Role, UnitConfig
from src.domain.task_rules import task_roles_list


def _task_roles_list(task: Task) -> list:
    # Backward-compatible import path while call sites migrate.
    return task_roles_list(task)


def _log(msg: str) -> None:
    print(f"[Allocator] {msg}", file=sys.stderr)


def _ceil_minute(dt: datetime) -> datetime:
    """Round a datetime UP to the next whole minute.

    The app stores daily presence intervals ending at 23:59:59, which
    creates a 1-second gap before midnight.  The allocation grid is
    5-minute-aligned so sub-minute precision is irrelevant.  Rounding
    up eliminates that gap: 23:59:59 → next-day 00:00:00.
    """
    if dt.second or dt.microsecond:
        return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return dt.replace(second=0, microsecond=0)


@dataclass
class SoldierState:
    id: int
    roles: List[str]
    is_active: bool
    presence_intervals: List[PresenceInterval]
    day_points: float
    night_points: float


@dataclass
class TaskSpec:
    id: int
    real_title: str
    start_time: datetime
    end_time: datetime
    is_fractionable: bool
    is_night: bool
    required_roles: List[str]
    concurrent_required: int
    hardness: int
    min_block_minutes: int
    readiness_minutes: int
    base_weight: float = 1.0
    excluded_soldier_ids: list[int] = None
    include_commander: bool = False

    def __post_init__(self):
        if self.excluded_soldier_ids is None:
            self.excluded_soldier_ids = []


@dataclass
class FrozenAssignment:
    soldier_id: int
    task_id: int
    start_time: datetime
    end_time: datetime


@dataclass
class PlannedAssignment:
    soldier_id: int
    task_id: int
    start_time: datetime
    end_time: datetime



class TaskAllocator:
    """
    The Fairness Engine.  Uses LP solver (PuLP + CBC) for global schedule
    optimization.  All tuning values are loaded from UnitConfig on init.

    See docs/LP_FORMULATION.md for the full mathematical specification.
    See src/core/lp_solver.py for the LP implementation.
    See src/core/lp_weights.py for tunable weights and constraint parameters.
    """

    def __init__(self, db: Session):
        self.db = db
        config = db.query(UnitConfig).first()

        if config:
            self.night_start_hour = config.night_start_hour
            self.night_end_hour = config.night_end_hour
            self.minimum_assignment_seconds = config.minimum_assignment_minutes * 60
            self._min_block_minutes_default = config.minimum_assignment_minutes
            self._readiness_default_minutes = config.availability_buffer_minutes
        else:
            self.night_start_hour = 23
            self.night_end_hour = 7
            self.minimum_assignment_seconds = 1800
            self._min_block_minutes_default = 30
            self._readiness_default_minutes = 60

        # During reconcile only: in-memory ledger for scoring. Cleared at end of run.
        self._run_ledger: dict[int, dict] | None = None
        # Per-run load tracking (hours assigned during this reconcile only).
        self._day_run_hours: dict[int, float] = {}
        self._night_run_hours: dict[int, float] = {}
        # After planning: soldiers flagged as overloaded for UI consumption.
        self.overloaded_soldiers: list[dict] = []

    # =========================================================
    # SNAPSHOT + LEDGER HELPERS
    # =========================================================

    def _build_decayed_excess_ledger(
        self, now: datetime, soldiers: list[Soldier], weights=None,
    ) -> dict[int, dict]:
        """Compute decayed load-above-average per soldier for day and night.

        For each soldier, looks back up to lookback_days and computes:
          excess[s] = Σ (daily_hours_above_unit_average × decay_rate^days_ago)

        Day and night are computed independently.
        Returns dict[soldier_id, {"day_points": float, "night_points": float}].
        """
        from src.core.lp_weights import LPWeights
        if weights is None:
            weights = LPWeights()

        lookback_start = now - timedelta(days=weights.lookback_days)
        night_start = self.night_start_hour
        night_end = self.night_end_hour

        # Fetch all past assignments within the lookback window.
        past_assignments = self.db.query(TaskAssignment).filter(
            TaskAssignment.end_time <= now,
            TaskAssignment.start_time >= lookback_start,
        ).all()

        # Build per-soldier per-date day/night hours.
        from collections import defaultdict
        # day_hours[date][soldier_id] = total day hours
        day_hours_by_date: dict = defaultdict(lambda: defaultdict(float))
        night_hours_by_date: dict = defaultdict(lambda: defaultdict(float))

        for a in past_assignments:
            dur_h = (a.end_time - a.start_time).total_seconds() / 3600.0
            d = a.start_time.date()
            h = a.start_time.hour
            if night_end <= night_start:
                is_night = h >= night_start or h < night_end
            else:
                is_night = night_start <= h < night_end
            if is_night:
                night_hours_by_date[d][a.soldier_id] += dur_h
            else:
                day_hours_by_date[d][a.soldier_id] += dur_h

        soldier_ids = {s.id for s in soldiers}

        # Compute decayed excess.
        ledger: dict[int, dict] = {
            s.id: {"day_points": 0.0, "night_points": 0.0}
            for s in soldiers
        }

        today = now.date()
        all_dates = set(day_hours_by_date.keys()) | set(night_hours_by_date.keys())

        for d in all_dates:
            days_ago = (today - d).days
            if days_ago < 0:
                continue
            decay = weights.decay_rate ** days_ago

            # Day excess.
            day_hrs = day_hours_by_date.get(d, {})
            if day_hrs:
                avg_day = sum(day_hrs.values()) / max(len(soldier_ids), 1)
                for sid in soldier_ids:
                    hrs = day_hrs.get(sid, 0.0)
                    ledger.setdefault(sid, {"day_points": 0.0, "night_points": 0.0})
                    ledger[sid]["day_points"] += (hrs - avg_day) * decay

            # Night excess.
            night_hrs = night_hours_by_date.get(d, {})
            if night_hrs:
                avg_night = sum(night_hrs.values()) / max(len(soldier_ids), 1)
                for sid in soldier_ids:
                    hrs = night_hrs.get(sid, 0.0)
                    ledger.setdefault(sid, {"day_points": 0.0, "night_points": 0.0})
                    ledger[sid]["night_points"] += (hrs - avg_night) * decay

        return ledger

    def _expand_roles_with_ancestors(self, role_names: list[str]) -> list[str]:
        """Expand a list of role names to include all ancestor roles.

        E.g. ["Operational Driver"] → ["Operational Driver", "Driver"]
        if Operational Driver's parent is Driver in the Role table.

        "Soldier" is excluded from expansion — it's the universal wildcard,
        not a meaningful inherited role.
        """
        if not role_names:
            return role_names

        all_roles = {r.name: r for r in self.db.query(Role).all()}
        expanded = set(role_names)
        for name in role_names:
            role = all_roles.get(name)
            visited: set[str] = set()
            while role and role.parent_role_id is not None and role.name not in visited:
                visited.add(role.name)
                parent = next((r for r in all_roles.values() if r.id == role.parent_role_id), None)
                if not parent:
                    break
                if parent.name != "Soldier":
                    expanded.add(parent.name)
                role = parent
        return list(expanded)

    def _build_soldier_states(
        self,
        now: datetime,
        soldiers: list[Soldier],
    ) -> list[SoldierState]:
        """
        Build SoldierState snapshots from ORM soldiers.
        Soldier roles are expanded to include ancestor roles (e.g. "Operational
        Driver" → ["Operational Driver", "Driver"]) so the LP solver's simple
        string matching handles role inheritance.

        day_points / night_points are read from persisted columns (populated by
        resync_soldier_rates).
        """
        states: list[SoldierState] = []

        for s in soldiers:
            states.append(
                SoldierState(
                    id=s.id,
                    roles=self._expand_roles_with_ancestors(list(s.role or [])),
                    is_active=bool(s.is_active_in_kav),
                    presence_intervals=list(s.presence or []),
                    day_points=float(s.total_day_points or 0.0),
                    night_points=float(s.total_night_points or 0.0),
                )
            )

        return states

    def _build_task_specs(
        self,
        tasks: list[Task],
    ) -> list[TaskSpec]:
        """Build TaskSpec snapshots from ORM tasks.

        Hardness comes from Task.hardness (1–5; default 3) and
        min_block_minutes follows the unit-wide minimum assignment setting
        unless overridden on the task.
        """
        specs: list[TaskSpec] = []

        for t in tasks:
            roles = _task_roles_list(t)
            # Current engine semantics: concurrency is number of required roles
            # (or 1 when only generic soldiers are needed).
            concurrent = len(roles) or 1

            hardness = getattr(t, "hardness", None)
            if hardness is None:
                hardness = 3

            excluded = list(getattr(t, "excluded_soldier_ids", None) or [])
            inc_cmd = bool(getattr(t, "include_commander", False))

            specs.append(
                TaskSpec(
                    id=t.id,
                    real_title=t.real_title,
                    start_time=t.start_time,
                    end_time=t.end_time,
                    is_fractionable=bool(t.is_fractionable),
                    is_night=self._is_night_window(t.start_time),
                    required_roles=roles,
                    concurrent_required=concurrent,
                    hardness=int(hardness),
                    min_block_minutes=getattr(t, "min_block_minutes", self._min_block_minutes_default),
                    readiness_minutes=t.readiness_minutes or self._readiness_default_minutes,
                    base_weight=float(getattr(t, "base_weight", 1.0) or 1.0),
                    excluded_soldier_ids=excluded,
                    include_commander=inc_cmd,
                )
            )

        return specs

    def _classify_and_freeze_assignments(self, now: datetime) -> set[int]:
        """
        Classify existing assignments and return the IDs of those that should
        be frozen (kept intact).  An assignment is frozen if:
        - It is in-progress: start_time < now and end_time > now
        - The soldier is gearing up: start_time >= now but
          start_time - task.readiness_minutes < now

        Historical assignments (end_time <= now) are left alone implicitly
        (they won't be deleted).  Everything else is replannable.
        """
        # All assignments that extend beyond now.
        future_assignments = self.db.query(TaskAssignment).filter(
            TaskAssignment.end_time > now,
        ).all()

        # Build task lookup for readiness_minutes.
        task_ids = {a.task_id for a in future_assignments}
        tasks_by_id: dict[int, Task] = {}
        if task_ids:
            for t in self.db.query(Task).filter(Task.id.in_(task_ids)).all():
                tasks_by_id[t.id] = t

        frozen_ids: set[int] = set()
        for a in future_assignments:
            # Pinned (manually edited) assignments are always frozen.
            if getattr(a, 'is_pinned', False):
                frozen_ids.add(a.id)
                continue
            if a.start_time < now:
                # In-progress: freeze entire assignment.
                frozen_ids.add(a.id)
                continue
            # start_time >= now: check gearing-up.
            task = tasks_by_id.get(a.task_id)
            readiness = (task.readiness_minutes or 0) if task else 0
            gear_up_start = a.start_time - timedelta(minutes=readiness)
            if gear_up_start < now:
                # Soldier is gearing up: freeze entire assignment.
                frozen_ids.add(a.id)

        return frozen_ids

    def _build_frozen_assignments(
        self,
        now: datetime,
    ) -> list[FrozenAssignment]:
        """
        Build FrozenAssignment snapshots from surviving DB assignments whose
        end_time extends past now.  Called after _classify_and_freeze_assignments
        + delete, so only in-progress and gearing-up assignments remain in the
        DB with end_time > now.  These are returned as whole (unsplit) frozen
        assignments for the planner.
        """
        surviving = self.db.query(TaskAssignment).filter(
            TaskAssignment.end_time > now,
        ).all()

        return [
            FrozenAssignment(
                soldier_id=a.soldier_id,
                task_id=a.task_id,
                start_time=a.start_time,
                end_time=a.end_time,
            )
            for a in surviving
        ]

    # =========================================================
    # THE GLOBAL RECONCILER
    # =========================================================

    def reconcile_future(self) -> None:
        """
        Rebuilds all future assignments as a single atomic transaction.

        Freeze policy: freeze_point = now.  Assignments are classified as:
        - historical (end_time <= now): leave alone
        - in-progress (start_time < now): freeze entire assignment
        - gearing-up (start_time >= now but start - readiness < now): freeze
        - replannable: delete and rebuild
        """
        now = datetime.now()
        freeze_point = now

        # Classify existing assignments: freeze in-progress and gearing-up,
        # delete everything else that is replannable.
        frozen_ids = self._classify_and_freeze_assignments(now)

        # Delete replannable assignments (not frozen, not historical).
        replan_query = self.db.query(TaskAssignment).filter(
            TaskAssignment.end_time > now,
        )
        if frozen_ids:
            replan_query = replan_query.filter(
                ~TaskAssignment.id.in_(frozen_ids),
            )
        replan_query.delete(synchronize_session='fetch')

        tasks = self.db.query(Task).filter(
            Task.end_time > now,
            Task.is_active == True,
        ).order_by(Task.start_time).all()

        all_soldiers = self.db.query(Soldier).filter(Soldier.is_active_in_kav == True).all()

        soldier_states = self._build_soldier_states(now, all_soldiers)
        task_specs = self._build_task_specs(tasks)
        frozen_specs = self._build_frozen_assignments(now)

        # Planner core: compute planned assignments in memory only.
        planned_assignments, coverage_by_task_id = self.plan_schedule(
            soldier_states, task_specs, frozen_specs, freeze_point
        )

        # Writer: persist planned assignments and final coverage status.
        self._write_planned_assignments(planned_assignments)

        # Coverage audit: set coverage_status for grid-planned tasks.
        for task_id, status in coverage_by_task_id.items():
            task = self.db.query(Task).filter(Task.id == task_id).first()
            if task is not None:
                task.coverage_status = status
        if coverage_by_task_id:
            self.db.flush()

        self.db.commit()
        _log("Commit complete")

    def plan_schedule(
        self,
        soldiers: list[SoldierState],
        tasks: list[TaskSpec],
        frozen: list[FrozenAssignment],
        freeze_point: datetime,
    ) -> tuple[list[PlannedAssignment], dict[int, str]]:
        """
        Core planner entry point: given snapshot structures and a freeze point,
        produce planned assignments without touching the database.

        Uses the LP solver (PuLP + CBC) for global optimization.
        Returns (planned_assignments, coverage_by_task_id).
        """
        soldier_ids = [s.id for s in soldiers]
        if soldier_ids:
            orm_soldiers = self.db.query(Soldier).filter(Soldier.id.in_(soldier_ids)).all()
        else:
            orm_soldiers = self.db.query(Soldier).filter(Soldier.is_active_in_kav == True).all()

        now = datetime.now()

        from src.core.lp_weights import LPWeights
        weights = LPWeights()

        # Use decayed excess ledger for the two-stage block solver.
        effective_ledger = self._build_decayed_excess_ledger(now, orm_soldiers, weights)

        # Seed run ledger for overload flagging.
        self._run_ledger = {
            s.id: {
                "day_points": effective_ledger.get(s.id, {}).get("day_points", 0.0),
                "night_points": effective_ledger.get(s.id, {}).get("night_points", 0.0),
            }
            for s in orm_soldiers
        }
        self._day_run_hours = {s.id: 0.0 for s in orm_soldiers}
        self._night_run_hours = {s.id: 0.0 for s in orm_soldiers}

        # Load command chain from unit config.
        config = self.db.query(UnitConfig).first()
        command_chain = list(config.command_chain or []) if config else []

        from src.core.lp_solver import lp_solve

        solution = lp_solve(
            soldier_states=soldiers,
            task_specs=tasks,
            frozen_assignments=frozen,
            freeze_point=freeze_point,
            night_start_hour=self.night_start_hour,
            night_end_hour=self.night_end_hour,
            weights=weights,
            effective_ledger=effective_ledger,
            command_chain=command_chain,
        )

        if solution.status in ("optimal", "feasible"):
            planned = solution.assignments
            coverage_by_task_id = solution.coverage_status
            _log(f"LP solver: {solution.status}, "
                 f"{len(planned)} assignments, "
                 f"{solution.solve_time_seconds:.2f}s")

            # Update run hours for overload flagging.
            for pa in planned:
                dur_h = (pa.end_time - pa.start_time).total_seconds() / 3600.0
                spec = next((t for t in tasks if t.id == pa.task_id), None)
                if spec and spec.is_night:
                    self._night_run_hours[pa.soldier_id] = (
                        self._night_run_hours.get(pa.soldier_id, 0.0) + dur_h
                    )
                else:
                    self._day_run_hours[pa.soldier_id] = (
                        self._day_run_hours.get(pa.soldier_id, 0.0) + dur_h
                    )

            # Overload flagging.
            self._flag_overloaded_soldiers(orm_soldiers)
            return planned, coverage_by_task_id
        else:
            _log(f"LP solver returned {solution.status}")
            # Return empty — LP is the only planner now.
            return [], {t.id: "UNCOVERED" for t in tasks}

    def _flag_overloaded_soldiers(self, orm_soldiers: list) -> None:
        """Flag soldiers whose total run hours exceed 2× average."""
        self.overloaded_soldiers = []
        total_run_hours: dict[int, float] = {}
        for sid in self._night_run_hours:
            total_run_hours[sid] = (
                self._night_run_hours.get(sid, 0.0)
                + self._day_run_hours.get(sid, 0.0)
            )
        soldiers_with_hours = {sid: h for sid, h in total_run_hours.items() if h > 0}
        if soldiers_with_hours and orm_soldiers:
            avg_hours = sum(total_run_hours.values()) / len(orm_soldiers)
            threshold = avg_hours * 2.0
            for sid, hours in soldiers_with_hours.items():
                if avg_hours > 0 and hours > threshold:
                    soldier = next((s for s in orm_soldiers if s.id == sid), None)
                    self.overloaded_soldiers.append({
                        "soldier_id": sid,
                        "soldier_name": soldier.name if soldier else f"#{sid}",
                        "hours": round(hours, 2),
                        "avg_hours": round(avg_hours, 2),
                        "ratio": round(hours / avg_hours, 2) if avg_hours > 0 else 0.0,
                    })

    # =========================================================
    # UTILITIES
    # =========================================================

    def _is_night_window(self, dt: datetime) -> bool:
        return (dt.hour >= self.night_start_hour) or (dt.hour < self.night_end_hour)

    def _night_date(self, dt: datetime):
        """
        Map a datetime into a canonical night "date" bucket.
        Hours >= night_start_hour belong to that calendar day.
        Hours < night_end_hour belong to the previous day.
        """
        if dt.hour >= self.night_start_hour:
            return dt.date()
        # Early-morning hours are counted towards the previous night's date
        return (dt - timedelta(days=1)).date()

    # =========================================================
    # COMMIT + WRITER
    # =========================================================

    def _commit_assignment(self, soldier: Soldier, task: Task,
                            start: datetime, end: datetime, is_night: bool,
                            update_ledger: bool = True,
                            persist: bool = True) -> None:
        """
        Persist a TaskAssignment row. The in-memory _run_ledger is updated for
        scoring within a reconcile run.  Persisted soldier points are NOT
        touched here — they are recomputed by resync_soldier_rates() after
        reconcile completes.
        """
        duration_hours = (end - start).total_seconds() / 3600.0
        points_earned = (task.base_weight or 1.0) * duration_hours

        if self._run_ledger is not None and soldier.id in self._run_ledger:
            led = self._run_ledger[soldier.id]
            if is_night:
                led["night_points"] = led.get("night_points", 0.0) + points_earned
            else:
                led["day_points"] = led.get("day_points", 0.0) + points_earned

        if persist:
            self.db.add(
                TaskAssignment(
                    task_id=task.id,
                    soldier_id=soldier.id,
                    start_time=start,
                    end_time=end,
                    final_weight_applied=points_earned,
                )
            )
            self.db.flush()

    def _write_planned_assignments(self, planned: list[PlannedAssignment]) -> None:
        """
        Persist PlannedAssignment blocks as TaskAssignment rows.

        PlannerCore is responsible for coverage decisions and block geometry;
        this writer only materialises rows and honours points discipline
        (update_ledger=False for reconcile-created assignments).
        """
        if not planned:
            return

        # Simple cache to avoid repeated lookups.
        soldier_cache: dict[int, Soldier] = {}
        task_cache: dict[int, Task] = {}

        for pa in planned:
            # Ignore placeholder or invalid entries defensively.
            if pa.soldier_id is None or pa.soldier_id <= 0:
                continue
            soldier = soldier_cache.get(pa.soldier_id)
            if soldier is None:
                soldier = self.db.query(Soldier).filter(Soldier.id == pa.soldier_id).first()
                if not soldier:
                    continue
                soldier_cache[pa.soldier_id] = soldier

            task = task_cache.get(pa.task_id)
            if task is None:
                task = self.db.query(Task).filter(Task.id == pa.task_id).first()
                if not task:
                    continue
                task_cache[pa.task_id] = task

            # Trim grid blocks to the true task window as per DESIGN_DECISIONS:
            # persisted TaskAssignment rows never extend beyond the task.
            start_time = max(pa.start_time, task.start_time)
            end_time = min(pa.end_time, task.end_time)
            if end_time <= start_time:
                continue

            is_night = self._is_night_window(start_time)
            self._commit_assignment(
                soldier,
                task,
                start_time,
                end_time,
                is_night=is_night,
                update_ledger=False,
                persist=True,
            )
