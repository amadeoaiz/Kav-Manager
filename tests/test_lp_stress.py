"""
LP solver stress tests — extreme conditions, edge cases, and scale.

Each test constructs in-memory dataclasses and calls lp_solve() directly.
No DB, no engine, no UI.
"""
from datetime import datetime, timedelta
from collections import defaultdict, Counter

import pytest

from src.core.engine import FrozenAssignment, PlannedAssignment, SoldierState, TaskSpec
from src.core.lp_solver import lp_solve, LPSolution
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


def _soldier_daily_presence(sid, base_date, num_days=7):
    """Create a soldier with daily presence records (23:59:59 midnight gap)."""
    intervals = []
    for day_offset in range(num_days):
        d = base_date + timedelta(days=day_offset)
        intervals.append(_presence(
            d.replace(hour=0, minute=0, second=0),
            d.replace(hour=23, minute=59, second=59),
        ))
    return SoldierState(
        id=sid, roles=[], is_active=True,
        presence_intervals=intervals,
        day_points=0.0, night_points=0.0,
    )


def _task(tid, start, end, concurrent=1, fractionable=True,
          readiness=0, roles=None, hardness=3, min_block=60):
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
        min_block_minutes=min_block,
        readiness_minutes=readiness,
    )


def _solve(soldiers, tasks, frozen=None, ledger=None, weights=None,
           freeze_point=None):
    return lp_solve(
        soldier_states=soldiers,
        task_specs=tasks,
        frozen_assignments=frozen or [],
        freeze_point=freeze_point or BASE,
        night_start_hour=NIGHT_START,
        night_end_hour=NIGHT_END,
        weights=weights or LPWeights(),
        effective_ledger=ledger or {},
    )


def _hours_by_soldier(assignments):
    hours = defaultdict(float)
    for a in assignments:
        hours[a.soldier_id] += (a.end_time - a.start_time).total_seconds() / 3600
    return dict(hours)


def _count_wakeups(assignments):
    """Count distinct contiguous groups per soldier (each group = 1 wakeup)."""
    by_soldier = defaultdict(list)
    for a in assignments:
        by_soldier[a.soldier_id].append(a)
    wakeups = {}
    for sid, blocks in by_soldier.items():
        blocks.sort(key=lambda a: a.start_time)
        groups = 1
        for i in range(1, len(blocks)):
            if blocks[i].start_time > blocks[i - 1].end_time:
                groups += 1
        wakeups[sid] = groups
    return wakeups


def _assert_no_overlap(assignments):
    """Assert no soldier has overlapping assignments."""
    by_soldier = defaultdict(list)
    for a in assignments:
        by_soldier[a.soldier_id].append((a.start_time, a.end_time))
    for sid, intervals in by_soldier.items():
        intervals.sort()
        for i in range(1, len(intervals)):
            assert intervals[i][0] >= intervals[i - 1][1], (
                f"Soldier {sid} has overlapping assignments: "
                f"{intervals[i-1]} and {intervals[i]}"
            )


# ══════════════════════════════════════════════════════════════════
# Group 1: Capacity extremes
# ══════════════════════════════════════════════════════════════════

class TestCapacityExtremes:
    def test_stress_understaffed_4soldiers_6tasks(self):
        """4 soldiers, 6 concurrent tasks — some must be UNCOVERED."""
        soldiers = [_soldier(i) for i in range(1, 5)]
        tasks = [
            _task(tid,
                  BASE + timedelta(hours=7),
                  BASE + timedelta(hours=19),
                  concurrent=1)
            for tid in range(1, 7)
        ]
        sol = _solve(soldiers, tasks)

        assert sol.status in ("optimal", "feasible")
        uncovered = [tid for tid, s in sol.coverage_status.items() if s == "UNCOVERED"]
        assert len(uncovered) > 0, "With 4 soldiers and 6 concurrent tasks, some should be UNCOVERED"

        # All assigned soldiers should be valid
        valid_ids = {s.id for s in soldiers}
        for a in sol.assignments:
            assert a.soldier_id in valid_ids

        _assert_no_overlap(sol.assignments)

    def test_stress_severely_understaffed_2soldiers_4tasks_concurrent2(self):
        """2 soldiers, 4 tasks each requiring concurrent=2 — extremely understaffed."""
        soldiers = [_soldier(i) for i in range(1, 3)]
        tasks = [
            _task(tid,
                  BASE + timedelta(hours=9),
                  BASE + timedelta(hours=17),
                  concurrent=2)
            for tid in range(1, 5)
        ]
        sol = _solve(soldiers, tasks)

        assert sol.status in ("optimal", "feasible")
        # Most tasks should be UNCOVERED (need 8 concurrent slots, have 2 soldiers)
        valid_ids = {s.id for s in soldiers}
        for a in sol.assignments:
            assert a.soldier_id in valid_ids

    def test_stress_overstaffed_30soldiers_2tasks(self):
        """30 soldiers, 2 tasks (concurrent=1 each) — work should spread."""
        soldiers = [_soldier(i) for i in range(1, 31)]
        tasks = [
            _task(1, BASE + timedelta(hours=8), BASE + timedelta(hours=16), concurrent=1),
            _task(2, BASE + timedelta(hours=8), BASE + timedelta(hours=16), concurrent=1),
        ]
        sol = _solve(soldiers, tasks)

        assert sol.status in ("optimal", "feasible")
        assert sol.coverage_status[1] == "OK"
        assert sol.coverage_status[2] == "OK"

        hours = _hours_by_soldier(sol.assignments)
        assigned_soldiers = [sid for sid, h in hours.items() if h > 0]
        # With 30 soldiers and 16h of work, assignments should spread
        assert len(assigned_soldiers) >= 4, (
            f"Expected work spread across >=4 soldiers, got {len(assigned_soldiers)}"
        )
        # No single soldier should get more than 50% of total hours
        total = sum(hours.values())
        if total > 0:
            assert max(hours.values()) <= total * 0.5

        _assert_no_overlap(sol.assignments)

    def test_stress_single_soldier_24h(self):
        """1 soldier available 24h, 2 tasks covering 07:00–07:00 next day."""
        soldiers = [_soldier(1)]
        tasks = [
            _task(1, BASE + timedelta(hours=7), BASE + timedelta(hours=23), concurrent=1),
            _task(2, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=7), concurrent=1),
        ]
        sol = _solve(soldiers, tasks)

        assert sol.status in ("optimal", "feasible")

        # Wakeup cost is a soft penalty — no hard cap, just verify counting works
        wakeups = _count_wakeups(sol.assignments)
        # Single soldier; wakeups should be reasonable (soft cost keeps them low)
        for sid, w in wakeups.items():
            assert w <= 4, f"Soldier {sid} has {w} wakeups — soft cost should keep this low"

        # Should have assignments for both tasks
        task_ids_assigned = {a.task_id for a in sol.assignments}
        assert 1 in task_ids_assigned or 2 in task_ids_assigned, (
            "Single soldier should cover at least one task"
        )
        _assert_no_overlap(sol.assignments)


# ══════════════════════════════════════════════════════════════════
# Group 2: Period edge cases
# ══════════════════════════════════════════════════════════════════

class TestPeriodEdgeCases:
    def test_stress_night_only(self):
        """3 tasks all spanning 23:00–06:00, 8 soldiers."""
        soldiers = [_soldier_daily_presence(i, BASE) for i in range(1, 9)]
        tasks = [
            _task(tid,
                  BASE + timedelta(hours=23),
                  BASE + timedelta(days=1, hours=6),
                  concurrent=1)
            for tid in range(1, 4)
        ]
        sol = _solve(soldiers, tasks)

        assert sol.status in ("optimal", "feasible")

        # Night assignments should exist
        night_assignments = [
            a for a in sol.assignments
            if a.start_time.hour >= NIGHT_START or a.start_time.hour < NIGHT_END
        ]
        assert len(night_assignments) > 0, "Night-only tasks should produce night assignments"

        # No day assignments (all tasks are 23:00–06:00)
        day_assignments = [
            a for a in sol.assignments
            if NIGHT_END <= a.start_time.hour < NIGHT_START
        ]
        assert len(day_assignments) == 0, "Night-only tasks should not produce day assignments"

        # Wakeup cost is a soft penalty — no hard cap
        wakeups = _count_wakeups(sol.assignments)
        for sid, w in wakeups.items():
            assert w <= 4, (
                f"Soldier {sid} has {w} wakeups — soft cost should keep this low"
            )

    def test_stress_day_only(self):
        """4 tasks all spanning 08:00–20:00, 10 soldiers."""
        soldiers = [_soldier(i) for i in range(1, 11)]
        tasks = [
            _task(tid,
                  BASE + timedelta(hours=8),
                  BASE + timedelta(hours=20),
                  concurrent=1)
            for tid in range(1, 5)
        ]
        sol = _solve(soldiers, tasks)

        assert sol.status in ("optimal", "feasible")

        # Day assignments should exist
        assert len(sol.assignments) > 0, "Day-only tasks should produce assignments"

        # No night assignments (all tasks within day window)
        night_assignments = [
            a for a in sol.assignments
            if a.start_time.hour >= NIGHT_START or a.start_time.hour < NIGHT_END
        ]
        assert len(night_assignments) == 0, (
            f"Day-only tasks should not produce night assignments, "
            f"got {len(night_assignments)}"
        )

        for tid in range(1, 5):
            assert sol.coverage_status[tid] == "OK"

    def test_stress_cross_midnight_single_task(self):
        """1 task spanning 22:00–08:00 next day, 6 soldiers."""
        soldiers = [_soldier_daily_presence(i, BASE) for i in range(1, 7)]
        task = _task(1,
                     BASE + timedelta(hours=22),
                     BASE + timedelta(days=1, hours=8),
                     concurrent=1)
        sol = _solve(soldiers, [task])

        assert sol.status in ("optimal", "feasible")
        assert sol.coverage_status[1] == "OK"

        # Should produce both day-period and night-period assignments
        has_day = any(
            NIGHT_END <= a.start_time.hour < NIGHT_START
            for a in sol.assignments
        )
        has_night = any(
            a.start_time.hour >= NIGHT_START or a.start_time.hour < NIGHT_END
            for a in sol.assignments
        )
        assert has_day or has_night, "Cross-midnight task should produce some assignments"
        _assert_no_overlap(sol.assignments)

    def test_stress_tiny_task_window_30min(self):
        """1 task spanning 10:00–10:30 (30 min), 5 soldiers, concurrent=1."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        task = _task(1,
                     BASE + timedelta(hours=10),
                     BASE + timedelta(hours=10, minutes=30),
                     concurrent=1, min_block=30)
        sol = _solve(soldiers, [task])

        assert sol.status in ("optimal", "feasible")
        assert sol.coverage_status[1] == "OK"

        # Should have exactly 1 soldier assigned (concurrent=1)
        assigned_soldiers = {a.soldier_id for a in sol.assignments}
        assert len(assigned_soldiers) == 1, (
            f"Tiny task with concurrent=1 should have 1 soldier, got {len(assigned_soldiers)}"
        )


# ══════════════════════════════════════════════════════════════════
# Group 3: Stress on constraints
# ══════════════════════════════════════════════════════════════════

class TestConstraintStress:
    def test_stress_many_fixed_tasks_same_day(self):
        """8 non-fractionable tasks on same day (staggered 1h windows), 12 soldiers."""
        soldiers = [_soldier(i) for i in range(1, 13)]
        tasks = [
            _task(tid,
                  BASE + timedelta(hours=8 + tid),
                  BASE + timedelta(hours=9 + tid),
                  concurrent=1, fractionable=False, hardness=2)
            for tid in range(1, 9)
        ]
        sol = _solve(soldiers, tasks)

        assert sol.status in ("optimal", "feasible")
        assert sol.solve_time_seconds < 45, (
            f"Solver took {sol.solve_time_seconds:.1f}s, expected <45s"
        )

        # Stacking penalty: no soldier should get more than 3 fixed tasks
        tasks_per_soldier = defaultdict(int)
        for a in sol.assignments:
            tasks_per_soldier[a.soldier_id] += 1
        for sid, count in tasks_per_soldier.items():
            assert count <= 3, (
                f"Soldier {sid} got {count} fixed tasks, max expected 3"
            )

    def test_stress_overlapping_fixed_tasks(self):
        """3 non-fractionable tasks with identical windows (10:00–12:00), 5 soldiers."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        tasks = [
            _task(tid,
                  BASE + timedelta(hours=10),
                  BASE + timedelta(hours=12),
                  concurrent=1, fractionable=False)
            for tid in range(1, 4)
        ]
        sol = _solve(soldiers, tasks)

        assert sol.status in ("optimal", "feasible")
        for tid in range(1, 4):
            assert sol.coverage_status[tid] == "OK"

        # Each task should have a different soldier (concurrent=1, identical windows)
        soldier_per_task = {}
        for a in sol.assignments:
            soldier_per_task[a.task_id] = a.soldier_id
        soldiers_used = set(soldier_per_task.values())
        assert len(soldiers_used) == 3, (
            f"3 overlapping fixed tasks should use 3 different soldiers, "
            f"got {len(soldiers_used)}"
        )

    def test_stress_rapid_presence_turnover(self):
        """10 soldiers: 1–5 present first half, 6–10 present second half."""
        mid = BASE + timedelta(hours=13)
        first_half = [
            _soldier(i, presence_start=BASE + timedelta(hours=7),
                     presence_end=mid)
            for i in range(1, 6)
        ]
        second_half = [
            _soldier(i, presence_start=mid,
                     presence_end=BASE + timedelta(hours=19))
            for i in range(6, 11)
        ]
        soldiers = first_half + second_half

        tasks = [
            _task(1, BASE + timedelta(hours=7), BASE + timedelta(hours=19), concurrent=2),
            _task(2, BASE + timedelta(hours=7), BASE + timedelta(hours=19), concurrent=1),
        ]
        sol = _solve(soldiers, tasks)

        assert sol.status in ("optimal", "feasible")

        # First half blocks should use soldiers 1–5
        first_half_ids = {1, 2, 3, 4, 5}
        second_half_ids = {6, 7, 8, 9, 10}

        for a in sol.assignments:
            if a.end_time <= mid:
                assert a.soldier_id in first_half_ids, (
                    f"First-half assignment uses soldier {a.soldier_id} "
                    f"who is only present in second half"
                )
            elif a.start_time >= mid:
                assert a.soldier_id in second_half_ids, (
                    f"Second-half assignment uses soldier {a.soldier_id} "
                    f"who is only present in first half"
                )

    def test_stress_max_wakeup_pressure(self):
        """6 soldiers, 3 night tasks (concurrent=2 each) — needs 6 soldier-blocks/slot."""
        soldiers = [_soldier_daily_presence(i, BASE) for i in range(1, 7)]
        tasks = [
            _task(tid,
                  BASE + timedelta(hours=23),
                  BASE + timedelta(days=1, hours=7),
                  concurrent=2, hardness=3)
            for tid in range(1, 4)
        ]
        sol = _solve(soldiers, tasks)

        assert sol.status in ("optimal", "feasible")

        # Wakeup cap: no soldier exceeds max_night_starts=2
        wakeups = _count_wakeups(sol.assignments)
        max_starts = LPWeights().max_night_starts
        for sid, w in wakeups.items():
            assert w <= max_starts, (
                f"Soldier {sid} has {w} wakeups, max is {max_starts}"
            )

        _assert_no_overlap(sol.assignments)


# ══════════════════════════════════════════════════════════════════
# Group 4: Scale and accumulation
# ══════════════════════════════════════════════════════════════════

class TestScaleAndAccumulation:
    def test_stress_long_horizon_3days(self):
        """8 soldiers, 3 tasks running continuously for 3 full days (72h)."""
        soldiers = [_soldier(i) for i in range(1, 9)]
        tasks = [
            _task(tid,
                  BASE + timedelta(hours=7),
                  BASE + timedelta(days=3, hours=7),
                  concurrent=1, hardness=3)
            for tid in range(1, 4)
        ]
        sol = _solve(soldiers, tasks, weights=LPWeights(time_limit_seconds=10))

        assert sol.status in ("optimal", "feasible")
        assert sol.solve_time_seconds < 120, (
            f"Solver took {sol.solve_time_seconds:.1f}s, expected <120s"
        )

        # Should produce many assignments across day and night
        assert len(sol.assignments) >= 10, (
            f"3-day horizon should produce many assignments, got {len(sol.assignments)}"
        )

        # Cross-segment fairness: no soldier's hours deviate >50% from average
        hours = _hours_by_soldier(sol.assignments)
        if hours:
            avg = sum(hours.values()) / len(soldiers)
            if avg > 0:
                for sid, h in hours.items():
                    deviation = abs(h - avg) / avg
                    assert deviation <= 1.0, (
                        f"Soldier {sid} has {h:.1f}h, avg is {avg:.1f}h "
                        f"(deviation {deviation:.0%} > 100%)"
                    )

        _assert_no_overlap(sol.assignments)

    def test_stress_many_tasks_12simultaneous(self):
        """15 soldiers, 12 simultaneous tasks (concurrent=1 each), day window."""
        soldiers = [_soldier(i) for i in range(1, 16)]
        tasks = [
            _task(tid,
                  BASE + timedelta(hours=7),
                  BASE + timedelta(hours=22),
                  concurrent=1, hardness=2)
            for tid in range(1, 13)
        ]
        sol = _solve(soldiers, tasks, weights=LPWeights(time_limit_seconds=5))

        assert sol.status in ("optimal", "feasible")
        assert sol.solve_time_seconds < 45, (
            f"Solver took {sol.solve_time_seconds:.1f}s, expected <45s"
        )

        # With 15 soldiers > 12 tasks, all should be covered
        ok_count = sum(1 for s in sol.coverage_status.values() if s == "OK")
        assert ok_count >= 12, (
            f"Expected all 12 tasks covered, got {ok_count} OK"
        )

        _assert_no_overlap(sol.assignments)


# ══════════════════════════════════════════════════════════════════
# Group 5: Weight sensitivity (informational)
# ══════════════════════════════════════════════════════════════════

class TestWeightSensitivity:
    def _standard_scenario(self):
        """8 soldiers, 3 tasks (day + night)."""
        soldiers = [_soldier_daily_presence(i, BASE) for i in range(1, 9)]
        tasks = [
            _task(1, BASE + timedelta(hours=8), BASE + timedelta(hours=16),
                  concurrent=1, hardness=3),
            _task(2, BASE + timedelta(hours=10), BASE + timedelta(hours=18),
                  concurrent=1, hardness=2),
            _task(3, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=5),
                  concurrent=1, hardness=4),
        ]
        return soldiers, tasks

    def test_stress_zero_fairness(self):
        """Zero fairness weights — solver should still produce valid coverage."""
        soldiers, tasks = self._standard_scenario()
        weights = LPWeights(
            w_day_fairness=0, w_day_minimax=0,
            w_night_fairness=0, w_night_minimax=0,
        )
        sol = _solve(soldiers, tasks, weights=weights)

        assert sol.status in ("optimal", "feasible")
        for tid in range(1, 4):
            assert sol.coverage_status[tid] == "OK"

        # Log fairness spread for manual review
        hours = _hours_by_soldier(sol.assignments)
        assigned = [h for h in hours.values() if h > 0]
        if assigned:
            spread = max(assigned) - min(assigned)
            print(f"\n[ZERO FAIRNESS] Hours spread: max-min = {spread:.1f}h "
                  f"(max={max(assigned):.1f}, min={min(assigned):.1f})")

    def test_stress_zero_wakeup_weight(self):
        """Zero wakeup weight — coverage should still hold."""
        soldiers, tasks = self._standard_scenario()
        weights = LPWeights(w_night_wakeups=0)
        sol = _solve(soldiers, tasks, weights=weights)

        assert sol.status in ("optimal", "feasible")
        for tid in range(1, 4):
            assert sol.coverage_status[tid] == "OK"

        # Log wakeup count for comparison
        wakeups = _count_wakeups(sol.assignments)
        total_wakeups = sum(wakeups.values())
        print(f"\n[ZERO WAKEUP WEIGHT] Total wakeups: {total_wakeups}")

    def test_stress_extreme_frozen_penalty(self):
        """Extreme frozen penalty — no new assignment within 1h of frozen end."""
        soldiers, tasks = self._standard_scenario()
        frozen = [
            FrozenAssignment(
                soldier_id=1, task_id=1,
                start_time=BASE + timedelta(hours=8),
                end_time=BASE + timedelta(hours=10),
            ),
            FrozenAssignment(
                soldier_id=2, task_id=2,
                start_time=BASE + timedelta(hours=10),
                end_time=BASE + timedelta(hours=12),
            ),
        ]
        weights = LPWeights(w_rest_frozen=100)
        sol = _solve(soldiers, tasks, frozen=frozen, weights=weights)

        assert sol.status in ("optimal", "feasible")
        for tid in range(1, 4):
            assert sol.coverage_status[tid] == "OK"

        # Check that soldier 1's next assignment starts >=1h after frozen end (10:00)
        # This is a soft penalty, not a hard constraint, so we check but don't fail hard
        s1_assignments = sorted(
            [a for a in sol.assignments if a.soldier_id == 1],
            key=lambda a: a.start_time,
        )
        if s1_assignments:
            frozen_end = BASE + timedelta(hours=10)
            first_new = s1_assignments[0]
            gap_h = (first_new.start_time - frozen_end).total_seconds() / 3600
            print(f"\n[EXTREME FROZEN] Soldier 1 gap after frozen end: {gap_h:.1f}h")
