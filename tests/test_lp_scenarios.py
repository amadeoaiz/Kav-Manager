"""
Comprehensive scenario tests for the block-based LP solver.

Covers edge cases: freeze points, infeasibility, block generation,
role eligibility, day/night splits, overlap prevention, and output format.
"""
from datetime import datetime, timedelta
from collections import defaultdict, Counter

import pytest

from src.core.engine import FrozenAssignment, PlannedAssignment, SoldierState, TaskSpec
from src.core.lp_solver import (
    lp_solve, LPSolution,
    _night_gap_penalty_factor, _day_gap_penalty_factor,
)
from src.core.lp_weights import LPWeights
from src.core.models import PresenceInterval


# ── Helpers ──────────────────────────────────────────────────────

BASE = datetime(2026, 4, 1, 0, 0, 0)
NIGHT_START = 23
NIGHT_END = 7


def _presence(start, end):
    return PresenceInterval(
        soldier_id=0, start_time=start, end_time=end, status="PRESENT",
    )


def _soldier(sid, roles=None, points_day=0.0, points_night=0.0,
             presence_start=None, presence_end=None):
    ps = presence_start or BASE
    pe = presence_end or (BASE + timedelta(days=7))
    return SoldierState(
        id=sid,
        roles=roles or [],
        is_active=True,
        presence_intervals=[_presence(ps, pe)],
        day_points=points_day,
        night_points=points_night,
    )


def _task(tid, start, end, concurrent=1, fractionable=True,
          readiness=0, roles=None, hardness=3):
    return TaskSpec(
        id=tid,
        real_title=f"Task-{tid}",
        start_time=start,
        end_time=end,
        is_fractionable=fractionable,
        is_night=start.hour >= NIGHT_START or start.hour < NIGHT_END,
        required_roles=roles or ["Soldier"],
        concurrent_required=concurrent,
        hardness=hardness,
        min_block_minutes=60,
        readiness_minutes=readiness,
    )


def _solve(soldiers, tasks, frozen=None, ledger=None, weights=None):
    return lp_solve(
        soldier_states=soldiers,
        task_specs=tasks,
        frozen_assignments=frozen or [],
        freeze_point=BASE,
        night_start_hour=NIGHT_START,
        night_end_hour=NIGHT_END,
        weights=weights or LPWeights(),
        effective_ledger=ledger or {},
    )


def _hours_by_soldier(sol):
    """Return dict of soldier_id -> total assigned hours."""
    hours = defaultdict(float)
    for a in sol.assignments:
        hours[a.soldier_id] += (a.end_time - a.start_time).total_seconds() / 3600
    return dict(hours)


def _slot_counts(sol, task_id=None):
    """Return Counter of 15-min slot -> number of concurrent soldiers."""
    counts = Counter()
    for a in sol.assignments:
        if task_id is not None and a.task_id != task_id:
            continue
        cursor = a.start_time
        while cursor < a.end_time:
            counts[cursor] += 1
            cursor += timedelta(minutes=15)
    return counts


# ══════════════════════════════════════════════════════════════════
# Freeze point handling
# ══════════════════════════════════════════════════════════════════

class TestFreezePointHandling:
    def test_frozen_in_progress_not_reassigned(self):
        """Frozen (in-progress) assignment: solver should not reassign
        that soldier during the frozen block; remaining slots still covered."""
        soldiers = [_soldier(1), _soldier(2), _soldier(3)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))

        frozen = [FrozenAssignment(
            soldier_id=1, task_id=1,
            start_time=BASE + timedelta(hours=10),
            end_time=BASE + timedelta(hours=12),
        )]
        sol = _solve(soldiers, [task], frozen=frozen)

        assert sol.coverage_status[1] == "OK"
        # Soldier 1 should NOT have any LP-planned assignment overlapping 10:00-12:00.
        for a in sol.assignments:
            if a.soldier_id == 1:
                assert a.start_time >= BASE + timedelta(hours=12) or \
                       a.end_time <= BASE + timedelta(hours=10), \
                    "LP assignment for soldier 1 overlaps frozen period"

    def test_frozen_gearing_up_not_reassigned(self):
        """Gearing-up frozen assignment: soldier is blocked, remaining slots covered."""
        soldiers = [_soldier(1), _soldier(2), _soldier(3)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=16), concurrent=2)

        # Soldier 1 is frozen for 10:00-13:00 (gearing up + doing task).
        frozen = [FrozenAssignment(
            soldier_id=1, task_id=1,
            start_time=BASE + timedelta(hours=10),
            end_time=BASE + timedelta(hours=13),
        )]
        sol = _solve(soldiers, [task], frozen=frozen)

        assert sol.coverage_status[1] == "OK"
        for a in sol.assignments:
            if a.soldier_id == 1:
                assert a.start_time >= BASE + timedelta(hours=13) or \
                       a.end_time <= BASE + timedelta(hours=10), \
                    "LP assignment for soldier 1 overlaps frozen gearing-up period"

    def test_all_assignments_frozen_empty_planned(self):
        """When all slots are frozen, solver returns empty planned list;
        coverage comes entirely from frozen assignments."""
        soldiers = [_soldier(1)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=12),
                     fractionable=False)

        frozen = [FrozenAssignment(
            soldier_id=1, task_id=1,
            start_time=BASE + timedelta(hours=10),
            end_time=BASE + timedelta(hours=12),
        )]
        sol = _solve(soldiers, [task], frozen=frozen)

        assert sol.coverage_status[1] == "OK"
        # No LP-planned assignments needed for this task.
        planned_for_task = [a for a in sol.assignments if a.task_id == 1]
        assert len(planned_for_task) == 0, \
            f"Expected 0 planned assignments, got {len(planned_for_task)}"

    def test_frozen_partial_coverage_solver_fills_rest(self):
        """Frozen covers part of a task; solver fills the remainder."""
        soldiers = [_soldier(1), _soldier(2), _soldier(3)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=16))

        # Frozen covers first 3 hours.
        frozen = [FrozenAssignment(
            soldier_id=1, task_id=1,
            start_time=BASE + timedelta(hours=10),
            end_time=BASE + timedelta(hours=13),
        )]
        sol = _solve(soldiers, [task], frozen=frozen)

        assert sol.coverage_status[1] == "OK"
        # Planned assignments should cover at least 13:00-16:00.
        planned = [a for a in sol.assignments if a.task_id == 1]
        assert len(planned) >= 1


# ══════════════════════════════════════════════════════════════════
# Infeasibility / degradation
# ══════════════════════════════════════════════════════════════════

class TestInfeasibility:
    def test_one_soldier_for_concurrent_two(self):
        """Only 1 soldier for concurrent=2: uses slack, marks UNCOVERED, doesn't crash."""
        soldiers = [_soldier(1)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14), concurrent=2)
        sol = _solve(soldiers, [task])

        assert sol.coverage_status[1] == "UNCOVERED"
        assert 1 in sol.metrics.uncovered_task_ids
        # Should still have assignments for the 1 available soldier.
        assert sol.status in ("optimal", "feasible")

    def test_zero_soldiers_all_uncovered(self):
        """Zero soldiers: all tasks UNCOVERED, empty assignment list."""
        t1 = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        t2 = _task(2, BASE + timedelta(hours=14), BASE + timedelta(hours=18))
        sol = _solve([], [t1, t2])

        assert sol.coverage_status[1] == "UNCOVERED"
        assert sol.coverage_status[2] == "UNCOVERED"
        assert len(sol.assignments) == 0

    def test_soldier_available_only_part_of_block(self):
        """Soldier present for only part of task window should be excluded
        from blocks where they're absent."""
        # Soldier present only 10:00-11:00 but task is 10:00-14:00.
        s1 = _soldier(1, presence_start=BASE + timedelta(hours=10),
                       presence_end=BASE + timedelta(hours=11))
        s2 = _soldier(2)  # Present all week.
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        sol = _solve([s1, s2], [task])

        assert sol.coverage_status[1] == "OK"
        # Soldier 1 should only appear in assignments within their presence.
        for a in sol.assignments:
            if a.soldier_id == 1:
                assert a.start_time >= BASE + timedelta(hours=10)
                assert a.end_time <= BASE + timedelta(hours=11)

    def test_more_tasks_than_soldiers_partial_coverage(self):
        """More concurrent demand than soldiers can handle:
        some tasks UNCOVERED, others still covered."""
        soldiers = [_soldier(1), _soldier(2)]
        # 3 overlapping tasks each needing 1 soldier — only 2 can be covered.
        t1 = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        t2 = _task(2, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        t3 = _task(3, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        sol = _solve(soldiers, [t1, t2, t3])

        covered = sum(1 for s in sol.coverage_status.values() if s == "OK")
        uncovered = sum(1 for s in sol.coverage_status.values() if s == "UNCOVERED")
        # At least 1 must be uncovered (only 2 soldiers for 3 concurrent tasks).
        assert uncovered >= 1, f"Expected at least 1 UNCOVERED, got {uncovered}"
        assert covered >= 1, f"Expected at least 1 OK, got {covered}"

    def test_infeasible_does_not_crash(self):
        """Massively infeasible scenario: 1 soldier, 5 overlapping tasks.
        Solver completes without raising exceptions."""
        soldiers = [_soldier(1)]
        tasks = [
            _task(i, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
            for i in range(1, 6)
        ]
        sol = _solve(soldiers, tasks)
        assert sol.status in ("optimal", "feasible")
        # At most 1 task can be fully covered.
        covered = sum(1 for s in sol.coverage_status.values() if s == "OK")
        assert covered <= 1


# ══════════════════════════════════════════════════════════════════
# Block generation edge cases
# ══════════════════════════════════════════════════════════════════

class TestBlockGenerationEdgeCases:
    def test_30min_task_becomes_one_block(self):
        """Task shorter than minimum block (30min): becomes 1 block, not skipped."""
        soldiers = [_soldier(1)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=10, minutes=30))
        sol = _solve(soldiers, [task])
        assert sol.coverage_status[1] == "OK"
        assert len(sol.assignments) >= 1

    def test_task_exactly_target_length(self):
        """Task exactly equal to a target length (90min): 1 block."""
        soldiers = [_soldier(1), _soldier(2)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=11, minutes=30))
        sol = _solve(soldiers, [task])
        assert sol.coverage_status[1] == "OK"
        assert len(sol.assignments) >= 1

    def test_task_within_single_block(self):
        """Task that starts and ends within a single block: covered in that block."""
        soldiers = [_soldier(1)]
        task = _task(1, BASE + timedelta(hours=10, minutes=15),
                     BASE + timedelta(hours=11, minutes=15))
        sol = _solve(soldiers, [task])
        assert sol.coverage_status[1] == "OK"

    def test_fixed_task_spanning_day_night_stays_single(self):
        """Fixed (non-fractionable) task spanning day and night: stays as single block."""
        soldiers = [_soldier(i) for i in range(1, 4)]
        task = _task(1, BASE + timedelta(hours=20), BASE + timedelta(days=1, hours=2),
                     fractionable=False)
        sol = _solve(soldiers, [task])
        assert sol.coverage_status[1] == "OK"
        # Each assigned soldier should cover the full duration.
        for a in sol.assignments:
            assert a.start_time == task.start_time
            assert a.end_time == task.end_time

    def test_very_short_fixed_task(self):
        """Very short non-fractionable task (15min): covered, not skipped."""
        soldiers = [_soldier(1)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=10, minutes=15),
                     fractionable=False)
        sol = _solve(soldiers, [task])
        assert sol.coverage_status[1] == "OK"


# ══════════════════════════════════════════════════════════════════
# Role eligibility
# ══════════════════════════════════════════════════════════════════

class TestRoleEligibilityScenarios:
    def test_driver_only_task(self):
        """Task requires 'Driver' role: only Driver-eligible soldiers assigned."""
        driver1 = _soldier(1, roles=["Driver"])
        driver2 = _soldier(2, roles=["Driver"])
        non_driver = _soldier(3, roles=["Medic"])
        generic = _soldier(4, roles=[])

        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                     roles=["Driver"])
        sol = _solve([driver1, driver2, non_driver, generic], [task])

        assert sol.coverage_status[1] == "OK"
        for a in sol.assignments:
            assert a.soldier_id in (1, 2), \
                f"Non-driver soldier {a.soldier_id} assigned to Driver task"

    def test_mixed_role_task(self):
        """Task needing 1 Driver + 3 Any: all 4 slots filled, at least 1 is a Driver."""
        drivers = [_soldier(i, roles=["Driver"]) for i in range(1, 3)]
        others = [_soldier(i) for i in range(3, 8)]
        all_soldiers = drivers + others

        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=12),
                     concurrent=4, fractionable=False,
                     roles=["Driver", "Soldier", "Soldier", "Soldier"])
        sol = _solve(all_soldiers, [task])

        assert sol.coverage_status[1] == "OK"
        assigned_ids = {a.soldier_id for a in sol.assignments if a.task_id == 1}
        assert len(assigned_ids) >= 4, f"Expected >=4 unique soldiers, got {len(assigned_ids)}"
        # At least one driver.
        driver_ids = {1, 2}
        assert assigned_ids & driver_ids, "No driver assigned to mixed-role task"

    def test_no_eligible_soldiers_uncovered(self):
        """No soldiers eligible for required role: task UNCOVERED."""
        soldiers = [_soldier(i, roles=["Medic"]) for i in range(1, 5)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                     roles=["Driver"])
        sol = _solve(soldiers, [task])

        assert sol.coverage_status[1] == "UNCOVERED"

    def test_role_and_general_soldier_tasks(self):
        """Driver task + general Soldier task: driver only on driver task,
        anyone on the other."""
        driver = _soldier(1, roles=["Driver"])
        grunts = [_soldier(i) for i in range(2, 5)]
        all_soldiers = [driver] + grunts

        driver_task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                            roles=["Driver"])
        any_task = _task(2, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                         roles=["Soldier"])
        sol = _solve(all_soldiers, [driver_task, any_task])

        assert sol.coverage_status[1] == "OK"
        assert sol.coverage_status[2] == "OK"
        for a in sol.assignments:
            if a.task_id == 1:
                assert a.soldier_id == 1, \
                    f"Non-driver {a.soldier_id} on driver task"


# ══════════════════════════════════════════════════════════════════
# Day / Night split
# ══════════════════════════════════════════════════════════════════

class TestDayNightSplit:
    def test_day_only_task_no_night_assignments(self):
        """Task entirely within day (10:00-16:00): no assignments in night window."""
        soldiers = [_soldier(i) for i in range(1, 5)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=16))
        sol = _solve(soldiers, [task])

        assert sol.coverage_status[1] == "OK"
        for a in sol.assignments:
            # All assignments should be within day window.
            assert a.start_time.hour >= NIGHT_END or a.start_time.hour < NIGHT_START

    def test_night_only_task_no_day_assignments(self):
        """Task entirely within night (00:00-05:00): all assignments in night window."""
        soldiers = [_soldier(i) for i in range(1, 4)]
        task = _task(1, BASE + timedelta(hours=0), BASE + timedelta(hours=5))
        sol = _solve(soldiers, [task])

        assert sol.coverage_status[1] == "OK"
        for a in sol.assignments:
            # All assignment times should be within night.
            assert a.start_time < BASE + timedelta(hours=NIGHT_END), \
                f"Assignment at {a.start_time} is outside night window"

    def test_task_crossing_day_night_boundary(self):
        """Task like Shmirat Yom 13:00-00:00: covered in both day and night periods."""
        soldiers = [_soldier(i) for i in range(1, 8)]
        task = _task(1, BASE + timedelta(hours=13), BASE + timedelta(days=1),
                     concurrent=2)
        sol = _solve(soldiers, [task])

        assert sol.coverage_status[1] == "OK"

        # Should have assignments before 23:00 (day) and after 23:00 (night).
        has_day = any(a.start_time < BASE + timedelta(hours=23) for a in sol.assignments)
        has_night = any(a.start_time >= BASE + timedelta(hours=23) for a in sol.assignments)
        assert has_day, "No day assignments for day-night crossing task"
        assert has_night, "No night assignments for day-night crossing task"

    def test_night_task_23_to_07(self):
        """Full night task 23:00-07:00: fully covered."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        task = _task(1, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=7))
        sol = _solve(soldiers, [task])

        assert sol.coverage_status[1] == "OK"
        # Total coverage should be ~8 hours.
        total = sum(
            (a.end_time - a.start_time).total_seconds() / 3600
            for a in sol.assignments if a.task_id == 1
        )
        assert total >= 7.5, f"Night coverage only {total:.1f}h, expected ~8h"


# ══════════════════════════════════════════════════════════════════
# Overlap prevention
# ══════════════════════════════════════════════════════════════════

class TestOverlapPrevention:
    def test_no_soldier_double_booked_same_block(self):
        """Soldier never assigned to two tasks in the same 15-min slot."""
        soldiers = [_soldier(i) for i in range(1, 4)]
        t1 = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        t2 = _task(2, BASE + timedelta(hours=12), BASE + timedelta(hours=16))
        sol = _solve(soldiers, [t1, t2])

        by_soldier = defaultdict(list)
        for a in sol.assignments:
            by_soldier[a.soldier_id].append((a.start_time, a.end_time))

        for sid, intervals in by_soldier.items():
            intervals.sort()
            for i in range(1, len(intervals)):
                assert intervals[i][0] >= intervals[i - 1][1], \
                    f"Soldier {sid} has overlapping assignments: " \
                    f"{intervals[i-1]} and {intervals[i]}"

    def test_no_overlap_with_many_concurrent_tasks(self):
        """3 overlapping tasks, 6 soldiers: no soldier double-booked."""
        soldiers = [_soldier(i) for i in range(1, 7)]
        t1 = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14), concurrent=2)
        t2 = _task(2, BASE + timedelta(hours=11), BASE + timedelta(hours=15), concurrent=2)
        t3 = _task(3, BASE + timedelta(hours=12), BASE + timedelta(hours=16), concurrent=2)
        sol = _solve(soldiers, [t1, t2, t3])

        by_soldier = defaultdict(list)
        for a in sol.assignments:
            by_soldier[a.soldier_id].append((a.start_time, a.end_time))

        for sid, intervals in by_soldier.items():
            intervals.sort()
            for i in range(1, len(intervals)):
                assert intervals[i][0] >= intervals[i - 1][1], \
                    f"Soldier {sid} double-booked: {intervals[i-1]} and {intervals[i]}"

    def test_no_overlap_frozen_and_planned(self):
        """Frozen assignment for soldier 1 on task A: solver must not plan
        soldier 1 on task B during that same window."""
        soldiers = [_soldier(1), _soldier(2), _soldier(3)]
        t1 = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        t2 = _task(2, BASE + timedelta(hours=10), BASE + timedelta(hours=14))

        frozen = [FrozenAssignment(
            soldier_id=1, task_id=1,
            start_time=BASE + timedelta(hours=10),
            end_time=BASE + timedelta(hours=12),
        )]
        sol = _solve(soldiers, [t1, t2], frozen=frozen)

        # Soldier 1 should not have planned assignments overlapping frozen.
        for a in sol.assignments:
            if a.soldier_id == 1:
                assert a.start_time >= BASE + timedelta(hours=12) or \
                       a.end_time <= BASE + timedelta(hours=10), \
                    f"Soldier 1 planned during frozen: {a.start_time}-{a.end_time}"


# ══════════════════════════════════════════════════════════════════
# Output format
# ══════════════════════════════════════════════════════════════════

class TestOutputFormat:
    def test_assignments_trimmed_to_task_window(self):
        """All PlannedAssignments trimmed to actual task window (not block boundaries)."""
        soldiers = [_soldier(i) for i in range(1, 4)]
        task = _task(1, BASE + timedelta(hours=10, minutes=20),
                     BASE + timedelta(hours=13, minutes=40))
        sol = _solve(soldiers, [task])

        for a in sol.assignments:
            assert a.start_time >= task.start_time, \
                f"Assignment starts {a.start_time} before task {task.start_time}"
            assert a.end_time <= task.end_time, \
                f"Assignment ends {a.end_time} after task {task.end_time}"

    def test_coverage_status_set_for_every_task(self):
        """Every task in input has a coverage_status entry."""
        soldiers = [_soldier(i) for i in range(1, 5)]
        tasks = [
            _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14)),
            _task(2, BASE + timedelta(hours=14), BASE + timedelta(hours=18)),
            _task(3, BASE + timedelta(hours=18), BASE + timedelta(hours=22)),
        ]
        sol = _solve(soldiers, tasks)

        for t in tasks:
            assert t.id in sol.coverage_status, \
                f"Task {t.id} missing from coverage_status"
            assert sol.coverage_status[t.id] in ("OK", "UNCOVERED"), \
                f"Invalid status '{sol.coverage_status[t.id]}' for task {t.id}"

    def test_no_zero_length_assignments(self):
        """No planned assignment should have start_time == end_time."""
        soldiers = [_soldier(i) for i in range(1, 4)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        sol = _solve(soldiers, [task])

        for a in sol.assignments:
            assert a.start_time < a.end_time, \
                f"Zero-length assignment: {a.start_time} to {a.end_time}"

    def test_assignment_soldier_ids_valid(self):
        """All assigned soldier IDs correspond to input soldiers."""
        soldiers = [_soldier(i) for i in range(1, 4)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        sol = _solve(soldiers, [task])

        valid_ids = {s.id for s in soldiers}
        for a in sol.assignments:
            assert a.soldier_id in valid_ids, \
                f"Assignment has invalid soldier_id {a.soldier_id}"

    def test_assignment_task_ids_valid(self):
        """All assigned task IDs correspond to input tasks."""
        soldiers = [_soldier(i) for i in range(1, 4)]
        tasks = [
            _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14)),
            _task(2, BASE + timedelta(hours=14), BASE + timedelta(hours=18)),
        ]
        sol = _solve(soldiers, tasks)

        valid_ids = {t.id for t in tasks}
        for a in sol.assignments:
            assert a.task_id in valid_ids, \
                f"Assignment has invalid task_id {a.task_id}"

    def test_no_overcoverage_per_slot(self):
        """No 15-min slot has more soldiers than concurrent_required."""
        soldiers = [_soldier(i) for i in range(1, 10)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14), concurrent=2)
        sol = _solve(soldiers, [task])

        counts = _slot_counts(sol, task_id=1)
        for slot_time, count in counts.items():
            assert count <= 2, \
                f"Slot {slot_time} has {count} soldiers, concurrent=2"


# ══════════════════════════════════════════════════════════════════
# Combined / integration scenarios
# ══════════════════════════════════════════════════════════════════

class TestIntegrationScenarios:
    def test_frozen_plus_role_constraint(self):
        """Frozen driver on driver task: solver should not reassign
        and should fill remaining slots respecting roles."""
        driver = _soldier(1, roles=["Driver"])
        soldiers = [driver] + [_soldier(i) for i in range(2, 5)]

        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                     roles=["Driver"])
        frozen = [FrozenAssignment(
            soldier_id=1, task_id=1,
            start_time=BASE + timedelta(hours=10),
            end_time=BASE + timedelta(hours=12),
        )]
        sol = _solve(soldiers, [task], frozen=frozen)

        assert sol.coverage_status[1] == "OK"
        # Only driver (soldier 1) should have assignments for this task.
        for a in sol.assignments:
            if a.task_id == 1:
                assert a.soldier_id == 1

    def test_multiple_frozen_different_tasks(self):
        """Multiple frozen assignments on different tasks: all respected."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        t1 = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        t2 = _task(2, BASE + timedelta(hours=14), BASE + timedelta(hours=18))

        frozen = [
            FrozenAssignment(soldier_id=1, task_id=1,
                             start_time=BASE + timedelta(hours=10),
                             end_time=BASE + timedelta(hours=12)),
            FrozenAssignment(soldier_id=2, task_id=2,
                             start_time=BASE + timedelta(hours=14),
                             end_time=BASE + timedelta(hours=16)),
        ]
        sol = _solve(soldiers, [t1, t2], frozen=frozen)

        assert sol.coverage_status[1] == "OK"
        assert sol.coverage_status[2] == "OK"

    def test_day_and_night_tasks_simultaneously(self):
        """Day task and night task: both covered, no cross-contamination."""
        soldiers = [_soldier(i) for i in range(1, 8)]
        day_task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        night_task = _task(2, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=5))
        sol = _solve(soldiers, [day_task, night_task])

        assert sol.coverage_status[1] == "OK"
        assert sol.coverage_status[2] == "OK"

    def test_full_day_scenario(self):
        """Realistic: 3 tasks across a day, 8 soldiers, various concurrencies."""
        soldiers = [_soldier(i) for i in range(1, 9)]
        morning = _task(1, BASE + timedelta(hours=8), BASE + timedelta(hours=12), concurrent=2)
        afternoon = _task(2, BASE + timedelta(hours=12), BASE + timedelta(hours=18))
        night = _task(3, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=7))
        sol = _solve(soldiers, [morning, afternoon, night])

        for tid in (1, 2, 3):
            assert sol.coverage_status[tid] == "OK"

        # No overlaps.
        by_soldier = defaultdict(list)
        for a in sol.assignments:
            by_soldier[a.soldier_id].append((a.start_time, a.end_time))
        for sid, intervals in by_soldier.items():
            intervals.sort()
            for i in range(1, len(intervals)):
                assert intervals[i][0] >= intervals[i - 1][1], \
                    f"Soldier {sid} overlap in full-day scenario"


# ══════════════════════════════════════════════════════════════════
# Day→night rest gap penalty
# ══════════════════════════════════════════════════════════════════

class TestDayToNightRestGap:
    def test_evening_assignment_avoids_early_night_when_alternatives_exist(self):
        """Soldier finishing a day task at 23:00 should not get a night block
        starting at 01:00 when other soldiers without day work are available."""
        soldiers = [_soldier(i) for i in range(1, 8)]  # 7 soldiers, plenty of slack
        day_task = _task(1, BASE + timedelta(hours=20), BASE + timedelta(hours=23))
        night_task = _task(2, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=7),
                          concurrent=2)
        sol = _solve(soldiers, [day_task, night_task])

        assert sol.coverage_status[1] == "OK"
        assert sol.coverage_status[2] == "OK"

        # Find who did the day task ending at 23:00
        day_soldiers = {a.soldier_id for a in sol.assignments
                        if a.task_id == 1}

        # Those soldiers should not appear in night blocks starting within 4h
        for a in sol.assignments:
            if a.task_id == 2 and a.soldier_id in day_soldiers:
                gap_h = (a.start_time - (BASE + timedelta(hours=23))).total_seconds() / 3600
                assert gap_h >= 4.0, \
                    f"Soldier {a.soldier_id} did day task to 23:00 then night at {a.start_time} (gap={gap_h:.1f}h)"

    def test_short_day_night_gap_allowed_when_no_alternative(self):
        """With very few soldiers, the LP should assign a short day→night gap
        rather than leave a block UNCOVERED."""
        soldiers = [_soldier(1), _soldier(2)]  # Only 2 soldiers
        day_task = _task(1, BASE + timedelta(hours=20), BASE + timedelta(hours=23))
        night_task = _task(2, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=5))
        sol = _solve(soldiers, [day_task, night_task])

        # Coverage is more important than rest — both tasks should be covered
        assert sol.coverage_status[1] == "OK"
        assert sol.coverage_status[2] == "OK"


class TestIdempotency:
    """Regression tests for reconcile idempotency (freeze_point clipping)."""

    def test_freeze_point_clips_task_start(self):
        """When freeze_point is mid-task, assignments must start >= freeze_point.

        This prevents a second reconcile from freezing first-run assignments
        as 'in-progress' and stacking duplicates on top.
        """
        # Task started 4 hours ago, ends 4 hours from now.
        task_start = BASE + timedelta(hours=8)
        task_end = BASE + timedelta(hours=16)
        freeze_point = BASE + timedelta(hours=12)  # 4 hours into the task

        soldiers = [_soldier(i) for i in range(1, 5)]
        task = _task(1, task_start, task_end, concurrent=1)

        sol = lp_solve(
            soldier_states=soldiers,
            task_specs=[task],
            frozen_assignments=[],
            freeze_point=freeze_point,
            night_start_hour=NIGHT_START,
            night_end_hour=NIGHT_END,
            weights=LPWeights(),
            effective_ledger={},
        )

        assert sol.status in ("optimal", "feasible")
        for a in sol.assignments:
            assert a.start_time >= freeze_point, (
                f"Assignment starts at {a.start_time}, before freeze_point {freeze_point}"
            )

    def test_two_solves_no_stacking(self):
        """Simulating two sequential reconciles: total assignments from run 2
        should not exceed what run 1 produced (no stacking)."""
        task_start = BASE + timedelta(hours=8)
        task_end = BASE + timedelta(hours=16)
        freeze_point = BASE + timedelta(hours=12)

        soldiers = [_soldier(i) for i in range(1, 5)]
        task = _task(1, task_start, task_end, concurrent=1)

        # Run 1
        sol1 = lp_solve(
            soldier_states=soldiers,
            task_specs=[_task(1, task_start, task_end, concurrent=1)],
            frozen_assignments=[],
            freeze_point=freeze_point,
            night_start_hour=NIGHT_START,
            night_end_hour=NIGHT_END,
            weights=LPWeights(),
            effective_ledger={},
        )

        # Run 2: freeze_point a few seconds later, assignments from run 1
        # are NOT frozen (they all start >= freeze_point from run 1).
        fp2 = freeze_point + timedelta(seconds=30)
        sol2 = lp_solve(
            soldier_states=soldiers,
            task_specs=[_task(1, task_start, task_end, concurrent=1)],
            frozen_assignments=[],
            freeze_point=fp2,
            night_start_hour=NIGHT_START,
            night_end_hour=NIGHT_END,
            weights=LPWeights(),
            effective_ledger={},
        )

        assert len(sol2.assignments) <= len(sol1.assignments) + 1, (
            f"Run 2 produced {len(sol2.assignments)} assignments vs "
            f"run 1's {len(sol1.assignments)} — possible stacking"
        )


# ══════════════════════════════════════════════════════════════════
# Night→day rest gap penalty
# ══════════════════════════════════════════════════════════════════

class TestNightToDayRestGap:
    def test_night_to_day_back_to_back_penalized(self):
        """Task window 23:00–09:00 with enough soldiers: no soldier should
        be assigned both the last night block and the first day block."""
        soldiers = [_soldier(i) for i in range(1, 9)]  # 8 soldiers, plenty of slack
        # Single task spanning night→day boundary.
        task = _task(1, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=9))
        sol = _solve(soldiers, [task])

        assert sol.coverage_status[1] == "OK"

        # Find soldiers assigned to blocks ending at or after 07:00 (night blocks)
        # and blocks starting at 07:00 (day blocks).
        night_boundary = BASE + timedelta(days=1, hours=7)
        soldiers_in_late_night = set()
        soldiers_in_early_day = set()
        for a in sol.assignments:
            if a.end_time > night_boundary - timedelta(minutes=15) and a.start_time < night_boundary:
                soldiers_in_late_night.add(a.soldier_id)
            if a.start_time >= night_boundary and a.start_time < night_boundary + timedelta(hours=1):
                soldiers_in_early_day.add(a.soldier_id)

        overlap = soldiers_in_late_night & soldiers_in_early_day
        assert len(overlap) == 0, (
            f"Soldiers {overlap} assigned to both late night and early day blocks "
            f"(back-to-back across night→day boundary)"
        )

    def test_night_to_day_still_covers_when_forced(self):
        """With only 2 soldiers for concurrent_required=1, both must share
        night and day blocks. Coverage is not sacrificed for rest — even if
        one soldier ends up with a back-to-back across the boundary."""
        soldiers = [_soldier(1), _soldier(2)]
        task = _task(1, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=9),
                     concurrent=1)
        sol = _solve(soldiers, [task])

        assert sol.coverage_status[1] == "OK"
        # Both soldiers should have assignments (fairness spreads the load).
        assigned_sids = {a.soldier_id for a in sol.assignments}
        assert len(assigned_sids) >= 1  # At minimum one soldier covers it all.

    def test_chronological_solve_order(self):
        """Task window spanning day→night→day (08:00–08:00+1d):
        no back-to-back at any transition boundary when enough soldiers."""
        soldiers = [_soldier(i) for i in range(1, 12)]  # 11 soldiers
        task = _task(1, BASE + timedelta(hours=8), BASE + timedelta(days=1, hours=8),
                     concurrent=2)
        sol = _solve(soldiers, [task])

        assert sol.coverage_status[1] == "OK"

        # Check no soldier has back-to-back assignments (gap < 30min)
        # at any transition boundary.
        by_soldier = defaultdict(list)
        for a in sol.assignments:
            by_soldier[a.soldier_id].append(a)

        for sid, asgns in by_soldier.items():
            asgns.sort(key=lambda x: x.start_time)
            for i in range(1, len(asgns)):
                gap_h = (asgns[i].start_time - asgns[i - 1].end_time).total_seconds() / 3600
                if gap_h < 0.01:
                    # Back-to-back — check it's not at a transition boundary.
                    boundary_23 = BASE + timedelta(hours=23)
                    boundary_07 = BASE + timedelta(days=1, hours=7)
                    at_boundary = (
                        abs((asgns[i].start_time - boundary_23).total_seconds()) < 60
                        or abs((asgns[i].start_time - boundary_07).total_seconds()) < 60
                    )
                    assert not at_boundary, (
                        f"Soldier {sid} has back-to-back at transition boundary: "
                        f"{asgns[i-1].end_time}→{asgns[i].start_time}"
                    )


# ══════════════════════════════════════════════════════════════════
# Cross-segment fairness carryover
# ══════════════════════════════════════════════════════════════════

class TestCrossSegmentFairnessCarryover:
    def test_day_load_carries_across_segments(self):
        """Two tasks creating two day segments. Soldiers who worked heavily
        in the first day segment should be less likely to be assigned in
        the second day segment."""
        soldiers = [_soldier(i) for i in range(1, 7)]  # 6 soldiers
        # Task A: day-only 08:00–12:00 (day segment 1).
        task_a = _task(1, BASE + timedelta(hours=8), BASE + timedelta(hours=12),
                       concurrent=2)
        # Task B: crosses night, 22:00–09:00 → produces day segments 22:00–23:00
        # and 07:00–09:00, plus night segment 23:00–07:00.
        task_b = _task(2, BASE + timedelta(hours=22), BASE + timedelta(days=1, hours=9))
        sol = _solve(soldiers, [task_a, task_b])

        assert sol.coverage_status[1] == "OK"
        assert sol.coverage_status[2] == "OK"

        # Compute per-soldier day hours (exclude night assignments 23:00–07:00).
        day_hours = defaultdict(float)
        for a in sol.assignments:
            # Day hours = everything outside 23:00–07:00.
            if a.start_time.hour >= 7 and a.start_time.hour < 23:
                day_hours[a.soldier_id] += (a.end_time - a.start_time).total_seconds() / 3600

        # With 6 soldiers and fairness carryover, no single soldier should
        # accumulate an extreme share of day hours.
        if day_hours:
            max_h = max(day_hours.values())
            avg_h = sum(day_hours.values()) / len(soldiers)
            # Max should not be more than 2.5× the average (fairness working).
            assert max_h <= avg_h * 2.5 + 0.5, (
                f"Unfair day distribution: max={max_h:.1f}h avg={avg_h:.1f}h "
                f"hours={dict(day_hours)}"
            )

    def test_night_load_carries_across_segments(self):
        """Two tasks creating two night segments. Soldiers who worked heavily
        in the first night segment should be less likely to be assigned in
        the second night segment."""
        soldiers = [_soldier(i) for i in range(1, 7)]  # 6 soldiers
        # Task A: night-only 23:00–03:00 (night segment 1).
        task_a = _task(1, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=3))
        # Task B: night-only 03:00–07:00 (night segment 2 — contiguous with A,
        # but separate task so the LP may process in one or two segments).
        # Use a gap to force separate segments.
        task_b = _task(2, BASE + timedelta(days=1, hours=3), BASE + timedelta(days=1, hours=7))
        sol = _solve(soldiers, [task_a, task_b])

        assert sol.coverage_status[1] == "OK"
        assert sol.coverage_status[2] == "OK"

        # Compute per-soldier night hours.
        night_hours = defaultdict(float)
        for a in sol.assignments:
            night_hours[a.soldier_id] += (a.end_time - a.start_time).total_seconds() / 3600

        # With 6 soldiers and fairness carryover, verify spread.
        if night_hours:
            max_h = max(night_hours.values())
            avg_h = sum(night_hours.values()) / len(soldiers)
            # Max should not be more than 2.5× the average.
            assert max_h <= avg_h * 2.5 + 0.5, (
                f"Unfair night distribution: max={max_h:.1f}h avg={avg_h:.1f}h "
                f"hours={dict(night_hours)}"
            )


# ══════════════════════════════════════════════════════════════════
# Stepped gap penalty functions
# ══════════════════════════════════════════════════════════════════

class TestSteppedGapPenalty:
    def test_night_penalty_values(self):
        """Unit test _night_gap_penalty_factor directly."""
        assert _night_gap_penalty_factor(0) == 12.0
        assert _night_gap_penalty_factor(0.5) == 12.0
        assert _night_gap_penalty_factor(1.0) == 12.0   # boundary: ≤1h
        assert _night_gap_penalty_factor(1.01) == 8.0
        assert _night_gap_penalty_factor(2) == 8.0
        assert _night_gap_penalty_factor(4) == 4.0
        assert _night_gap_penalty_factor(5.5) == 1.5
        assert _night_gap_penalty_factor(7) == 0.0

    def test_day_penalty_values(self):
        """Unit test _day_gap_penalty_factor directly."""
        assert _day_gap_penalty_factor(0) == 10.0
        assert _day_gap_penalty_factor(1.0) == 10.0     # boundary: ≤1h
        assert _day_gap_penalty_factor(1.01) == 5.0
        assert _day_gap_penalty_factor(2) == 5.0
        assert _day_gap_penalty_factor(4) == 2.0
        assert _day_gap_penalty_factor(5.5) == 0.0

    def test_day_gap_4h_now_penalized(self):
        """Two day blocks 4h apart: under old linear curve (3h threshold) this
        had zero penalty. Verify the LP prefers a different soldier."""
        soldiers = [_soldier(i) for i in range(1, 7)]  # 6 soldiers
        # Two tasks 4h apart — same soldier could do both, but penalty should discourage it.
        t1 = _task(1, BASE + timedelta(hours=8), BASE + timedelta(hours=10))
        t2 = _task(2, BASE + timedelta(hours=14), BASE + timedelta(hours=16))
        sol = _solve(soldiers, [t1, t2])

        assert sol.coverage_status[1] == "OK"
        assert sol.coverage_status[2] == "OK"

        # With enough soldiers and a 4h gap now penalized, no soldier should do both.
        sol1_soldiers = {a.soldier_id for a in sol.assignments if a.task_id == 1}
        sol2_soldiers = {a.soldier_id for a in sol.assignments if a.task_id == 2}
        overlap = sol1_soldiers & sol2_soldiers
        assert len(overlap) == 0, (
            f"Soldiers {overlap} assigned to both tasks with 4h gap — "
            f"should prefer different soldiers"
        )


# ══════════════════════════════════════════════════════════════════
# Fixed-task spreading
# ══════════════════════════════════════════════════════════════════

class TestFixedTaskSpreading:
    def test_fixed_tasks_spread_across_soldiers(self):
        """Two non-overlapping same-day fixed tasks (Kitchen 12-16, Patrol 18:30-21),
        6+ soldiers available. No soldier should be assigned to both."""
        soldiers = [_soldier(i) for i in range(1, 8)]  # 7 soldiers
        kitchen = _task(101, BASE + timedelta(hours=12), BASE + timedelta(hours=16),
                        fractionable=False)
        patrol = _task(102, BASE + timedelta(hours=18, minutes=30),
                       BASE + timedelta(hours=21), fractionable=False)
        sol = _solve(soldiers, [kitchen, patrol])

        assert sol.coverage_status[101] == "OK"
        assert sol.coverage_status[102] == "OK"

        kitchen_soldiers = {a.soldier_id for a in sol.assignments if a.task_id == 101}
        patrol_soldiers = {a.soldier_id for a in sol.assignments if a.task_id == 102}
        overlap = kitchen_soldiers & patrol_soldiers
        assert len(overlap) == 0, (
            f"Soldiers {overlap} assigned to both Kitchen and Patrol on same day"
        )

    def test_fixed_tasks_stack_when_forced(self):
        """Same two tasks but only 2 eligible soldiers. Coverage still wins."""
        soldiers = [_soldier(1), _soldier(2)]
        kitchen = _task(101, BASE + timedelta(hours=12), BASE + timedelta(hours=16),
                        fractionable=False)
        patrol = _task(102, BASE + timedelta(hours=18, minutes=30),
                       BASE + timedelta(hours=21), fractionable=False)
        sol = _solve(soldiers, [kitchen, patrol])

        assert sol.coverage_status[101] == "OK"
        assert sol.coverage_status[102] == "OK"

    def test_fixed_tasks_different_days_no_penalty(self):
        """Two fixed tasks on different days, same soldier eligible.
        Solver freely assigns same soldier to both."""
        soldiers = [_soldier(1), _soldier(2)]
        day1_task = _task(101, BASE + timedelta(hours=12), BASE + timedelta(hours=16),
                          fractionable=False)
        day2_task = _task(102, BASE + timedelta(days=1, hours=12),
                          BASE + timedelta(days=1, hours=16), fractionable=False)
        sol = _solve(soldiers, [day1_task, day2_task])

        assert sol.coverage_status[101] == "OK"
        assert sol.coverage_status[102] == "OK"
        # No stacking penalty across days — same soldier may get both.
        # (We just verify coverage, not that they spread — with only 2 soldiers
        #  either outcome is valid.)

    def test_fixed_task_excess_aware(self):
        """Two soldiers eligible for a fixed task, one with high day_excess,
        one with zero. Low-excess soldier should be preferred."""
        soldiers = [_soldier(1), _soldier(2)]
        task = _task(101, BASE + timedelta(hours=12), BASE + timedelta(hours=16),
                     fractionable=False)
        ledger = {
            1: {"day_points": 5.0, "night_points": 0.0},
            2: {"day_points": 0.0, "night_points": 0.0},
        }
        sol = _solve(soldiers, [task], ledger=ledger)

        assert sol.coverage_status[101] == "OK"
        assigned = {a.soldier_id for a in sol.assignments if a.task_id == 101}
        assert 2 in assigned, (
            f"Expected low-excess soldier 2 to be preferred, got {assigned}"
        )


class TestNightQualitySteering:
    """Verify night quality steering assigns worse blocks to low-excess soldiers."""

    def test_low_excess_gets_worse_blocks(self):
        """Tier-3 (middle-of-night) blocks should NOT go to high-excess soldiers.

        Setup: 3 soldiers, concurrent=1, night task 23:00–07:00, 180min blocks.
        Produces 3 blocks (1 per soldier). High-excess soldiers pay more for bad
        blocks, so the tier-3 block goes to the low-excess soldier.
        """
        night_start = BASE + timedelta(hours=23)
        night_end = BASE + timedelta(days=1, hours=7)
        task = _task(1, night_start, night_end, concurrent=1, hardness=3)

        soldiers = [
            _soldier(1, points_night=0.0),
            _soldier(2, points_night=0.0),
            _soldier(3, points_night=0.0),
        ]
        ledger = {
            1: {"day_points": 0.0, "night_points": 0.0},   # Low excess
            2: {"day_points": 0.0, "night_points": 8.0},   # High excess
            3: {"day_points": 0.0, "night_points": 8.0},   # High excess
        }
        # Boost steering weight so it dominates over excess cost noise.
        weights = LPWeights(target_block_lengths=[180], w_night_hardship=15)
        sol = _solve(soldiers, [task], ledger=ledger, weights=weights)
        assert sol.coverage_status[1] == "OK"

        # Find the tier-3 block (midpoint in middle half of night: ~01:00–05:00).
        # The high-excess soldiers (2, 3) should NOT have it.
        high_excess_ids = {2, 3}
        for a in sol.assignments:
            if a.soldier_id in high_excess_ids:
                mid = a.start_time + (a.end_time - a.start_time) / 2
                h = mid.hour + mid.minute / 60.0
                assert not (1 <= h <= 5), (
                    f"High-excess soldier {a.soldier_id} should not get tier-3 block, "
                    f"got {a.start_time.strftime('%H:%M')}-{a.end_time.strftime('%H:%M')} (mid={h:.1f}h)"
                )

    def test_high_excess_gets_better_blocks(self):
        """High night-excess soldier should be steered to better blocks (tier 1/2)."""
        night_start = BASE + timedelta(hours=23)
        night_end = BASE + timedelta(days=1, hours=7)
        task = _task(1, night_start, night_end, concurrent=1, hardness=3)

        soldiers = [
            _soldier(1, points_night=0.0),
            _soldier(2, points_night=0.0),
            _soldier(3, points_night=0.0),
            _soldier(4, points_night=0.0),
            _soldier(5, points_night=0.0),
        ]
        ledger = {
            1: {"day_points": 0.0, "night_points": 10.0},  # Highest excess
            2: {"day_points": 0.0, "night_points": 0.0},   # Low excess
            3: {"day_points": 0.0, "night_points": 0.0},   # Low excess
            4: {"day_points": 0.0, "night_points": 0.0},   # Low excess
            5: {"day_points": 0.0, "night_points": 0.0},   # Low excess
        }
        sol = _solve(soldiers, [task], ledger=ledger)
        assert sol.coverage_status[1] == "OK"

        s1_assignments = [a for a in sol.assignments if a.soldier_id == 1]
        assert len(s1_assignments) >= 1, "High-excess soldier should still be assigned"

        # Soldier 1 (high excess) should get fewer weighted night hours than
        # the average soldier.  Quality steering directs them to better (lower
        # tier) blocks, so their h × tier product should be lower.
        def _weighted_hours(asgn_list):
            """Sum h × simple_tier for a list of assignments."""
            total = 0.0
            for a in asgn_list:
                h = (a.end_time - a.start_time).total_seconds() / 3600.0
                mid = a.start_time + (a.end_time - a.start_time) / 2
                mid_h = mid.hour + mid.minute / 60.0
                # Simple tier: 1=edge, 3=deep night.
                tier = 3 if 1 <= mid_h <= 5 else 1
                total += h * tier
            return total

        s1_wh = _weighted_hours(s1_assignments)
        other_wh = []
        for sid in [2, 3, 4, 5]:
            sa = [a for a in sol.assignments if a.soldier_id == sid]
            if sa:
                other_wh.append(_weighted_hours(sa))
        if other_wh:
            avg_other_wh = sum(other_wh) / len(other_wh)
            assert s1_wh <= avg_other_wh + 0.5, (
                f"High-excess soldier 1 weighted_hours={s1_wh:.1f} should be "
                f"<= avg others={avg_other_wh:.1f} (quality steering)"
            )


class TestFrozenGapPenalty:
    """Verify that frozen/fixed → block gap penalty outweighs fairness."""

    def test_frozen_gap_outweighs_fairness(self):
        """Soldier with a fixed Patrol should not get the adjacent Day Guard block."""
        # Day Guard 11:00–21:00, req=2 (fractionable)
        # Patrol 18:30–21:00, req=1 (fixed, assigned to soldier 1 by fixed LP)
        day_guard = _task(1, BASE + timedelta(hours=11), BASE + timedelta(hours=21),
                          concurrent=2, fractionable=True)
        patrol = _task(2, BASE + timedelta(hours=18, minutes=30),
                       BASE + timedelta(hours=21), concurrent=1, fractionable=False)

        # 6 soldiers, soldier 1 has low excess (fairness would prefer them)
        soldiers = [_soldier(i) for i in range(1, 7)]
        ledger = {
            1: {"day_points": -2.0, "night_points": 0.0},  # Below average — fairness wants to assign them
            2: {"day_points": 0.0, "night_points": 0.0},
            3: {"day_points": 0.0, "night_points": 0.0},
            4: {"day_points": 0.0, "night_points": 0.0},
            5: {"day_points": 0.0, "night_points": 0.0},
            6: {"day_points": 0.0, "night_points": 0.0},
        }
        sol = _solve(soldiers, [day_guard, patrol], ledger=ledger)
        assert sol.coverage_status[1] == "OK"
        assert sol.coverage_status[2] == "OK"

        # Find which soldier got patrol
        patrol_soldier = None
        for a in sol.assignments:
            if a.task_id == 2:
                patrol_soldier = a.soldier_id
                break
        assert patrol_soldier is not None

        # Check that the patrol soldier doesn't have a Day Guard block ending
        # right before patrol (within 2h gap).
        patrol_start = BASE + timedelta(hours=18, minutes=30)
        dg_blocks_for_patrol_soldier = [
            a for a in sol.assignments
            if a.task_id == 1 and a.soldier_id == patrol_soldier
        ]
        for a in dg_blocks_for_patrol_soldier:
            gap_h = (patrol_start - a.end_time).total_seconds() / 3600
            if 0 < gap_h < 2.0:
                # This is the tight gap we want to avoid
                pytest.fail(
                    f"Soldier {patrol_soldier} got Day Guard {a.start_time.strftime('%H:%M')}-"
                    f"{a.end_time.strftime('%H:%M')} with only {gap_h:.1f}h gap before "
                    f"Patrol at {patrol_start.strftime('%H:%M')}"
                )

    def test_frozen_gap_yields_to_coverage(self):
        """With only 2 soldiers, coverage wins over gap avoidance."""
        day_guard = _task(1, BASE + timedelta(hours=11), BASE + timedelta(hours=21),
                          concurrent=1, fractionable=True)
        patrol = _task(2, BASE + timedelta(hours=18, minutes=30),
                       BASE + timedelta(hours=21), concurrent=1, fractionable=False)

        soldiers = [_soldier(1), _soldier(2)]
        sol = _solve(soldiers, [day_guard, patrol])

        # Coverage is the priority — both tasks must be covered.
        assert sol.coverage_status[1] == "OK"
        assert sol.coverage_status[2] == "OK"
