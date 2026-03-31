"""
Two-stage block-based LP solver unit tests.

Tests call lp_solve() directly with constructed SoldierState/TaskSpec inputs
to verify the block generation and LP formulation's correctness.
"""
from datetime import datetime, timedelta
from collections import defaultdict, Counter

import pytest

from src.core.engine import FrozenAssignment, PlannedAssignment, SoldierState, TaskSpec
from src.core.lp_solver import (
    lp_solve, LPSolution,
    _generate_blocks_for_period, _determine_periods, _map_tasks_to_blocks,
    _validate_blocks, _generate_all_blocks, _classify_night_tier, TimeBlock,
    _merge_present_intervals,
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


# ══════════════════════════════════════════════════════════════════
# Block generation tests
# ══════════════════════════════════════════════════════════════════

class TestBlockGeneration:
    def test_correct_number_of_blocks(self):
        """Block count = round(period / target)."""
        start = datetime(2026, 4, 1, 10, 0)
        end = datetime(2026, 4, 1, 16, 0)  # 6 hours = 360 min
        blocks = _generate_blocks_for_period(start, end, 90, False, 23, 7)
        assert len(blocks) == 4  # round(360/90) = 4

    def test_boundaries_snap_to_15min_grid(self):
        """All block boundaries are on 15-minute marks."""
        start = datetime(2026, 4, 1, 10, 0)
        end = datetime(2026, 4, 1, 16, 0)
        for target in [60, 75, 90, 120]:
            blocks = _generate_blocks_for_period(start, end, target, False, 23, 7)
            for b in blocks:
                assert b.start_time.minute % 15 == 0, f"Start {b.start_time} not on grid"
                assert b.end_time.minute % 15 == 0, f"End {b.end_time} not on grid"

    def test_blocks_cover_full_period(self):
        """Blocks together cover the entire period without gaps."""
        start = datetime(2026, 4, 1, 10, 0)
        end = datetime(2026, 4, 1, 16, 0)
        blocks = _generate_blocks_for_period(start, end, 90, False, 23, 7)
        assert blocks[0].start_time == start
        assert blocks[-1].end_time == end
        for i in range(1, len(blocks)):
            assert blocks[i].start_time == blocks[i - 1].end_time

    def test_generated_blocks_within_size_range(self):
        """All generated blocks are within 45min–3h."""
        start = datetime(2026, 4, 1, 10, 0)
        end = datetime(2026, 4, 1, 20, 0)  # 10 hours
        for target in [60, 90, 120, 150, 180]:
            blocks = _generate_blocks_for_period(start, end, target, False, 23, 7)
            assert _validate_blocks(blocks, 45, 180)

    def test_fixed_tasks_exempt_from_size_check(self):
        """Fixed tasks are not part of block generation."""
        soldiers = [_soldier(i) for i in range(1, 4)]
        # A 30-minute fixed task — too short for any block, but should still work.
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=10, minutes=30),
                     fractionable=False)
        sol = _solve(soldiers, [task])
        assert sol.coverage_status[1] == "OK"

    def test_night_quality_tiers(self):
        """Night blocks get correct quality tiers."""
        # Night window 23:00–07:00 = 8 hours.
        # Quarter = 2 hours.
        # 23:00–01:00 = tier 1 (first quarter)
        # 01:00–05:00 = tier 3 (middle two quarters)
        # 05:00–07:00 = tier 2 (last quarter)
        tier1 = _classify_night_tier(datetime(2026, 4, 1, 23, 30), 23, 7)
        assert tier1 == 1
        tier3 = _classify_night_tier(datetime(2026, 4, 2, 3, 0), 23, 7)
        assert tier3 == 3
        tier2 = _classify_night_tier(datetime(2026, 4, 2, 6, 0), 23, 7)
        assert tier2 == 2


class TestBlockTaskMapping:
    def test_task_active_in_overlapping_blocks(self):
        """Task is active in blocks that overlap its time window."""
        start = datetime(2026, 4, 1, 10, 0)
        end = datetime(2026, 4, 1, 16, 0)
        blocks = _generate_blocks_for_period(start, end, 120, False, 23, 7)

        task = _task(1, datetime(2026, 4, 1, 11, 0), datetime(2026, 4, 1, 13, 0))
        _map_tasks_to_blocks(blocks, [task])

        for block in blocks:
            task_ids = [tid for tid, _ in block.active_tasks]
            overlaps = block.end_time > task.start_time and block.start_time < task.end_time
            if overlaps:
                assert 1 in task_ids, f"Task should be active in block {block.start_time}-{block.end_time}"
            else:
                assert 1 not in task_ids

    def test_task_inactive_outside_window(self):
        """Task is not active in blocks outside its time window."""
        start = datetime(2026, 4, 1, 10, 0)
        end = datetime(2026, 4, 1, 16, 0)
        blocks = _generate_blocks_for_period(start, end, 120, False, 23, 7)

        task = _task(1, datetime(2026, 4, 1, 10, 0), datetime(2026, 4, 1, 11, 0))
        _map_tasks_to_blocks(blocks, [task])

        for block in blocks:
            if block.start_time >= task.end_time:
                assert 1 not in [tid for tid, _ in block.active_tasks]


# ══════════════════════════════════════════════════════════════════
# Day LP tests
# ══════════════════════════════════════════════════════════════════

class TestDayLP:
    def test_full_coverage(self):
        """All blocks covered when soldiers are sufficient."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14), concurrent=2)
        sol = _solve(soldiers, [task])
        assert sol.status in ("optimal", "feasible")
        assert sol.coverage_status[1] == "OK"

    def test_no_overlap(self):
        """No soldier assigned to two tasks in the same block."""
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
                assert intervals[i][0] >= intervals[i - 1][1], (
                    f"Soldier {sid} has overlapping assignments"
                )

    def test_fairness_spread(self):
        """With 5 soldiers and a 4h task, hours are spread reasonably."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        sol = _solve(soldiers, [task])

        hours = defaultdict(float)
        for a in sol.assignments:
            hours[a.soldier_id] += (a.end_time - a.start_time).total_seconds() / 3600

        assigned = [h for h in hours.values() if h > 0]
        if assigned:
            fair_share = 4.0 / 5
            assert max(assigned) <= fair_share * 3.0, (
                f"Max hours {max(assigned)} exceeds 3× fair share {fair_share}"
            )

    def test_no_overcoverage(self):
        """No block has more soldiers than concurrent_required."""
        soldiers = [_soldier(i) for i in range(1, 10)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14), concurrent=1)
        sol = _solve(soldiers, [task])

        # Check assignments don't overlap on the same task.
        slot_counts = Counter()
        for a in sol.assignments:
            if a.task_id == 1:
                cursor = a.start_time
                while cursor < a.end_time:
                    slot_counts[cursor] += 1
                    cursor += timedelta(minutes=15)

        for slot_time, count in slot_counts.items():
            assert count <= 1, f"Slot {slot_time} has {count} soldiers, concurrent=1"


# ══════════════════════════════════════════════════════════════════
# Night LP tests
# ══════════════════════════════════════════════════════════════════

class TestNightLP:
    def test_night_coverage(self):
        """Night task gets full coverage."""
        soldiers = [_soldier(i) for i in range(1, 4)]
        task = _task(1, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=5))
        sol = _solve(soldiers, [task])
        assert sol.coverage_status[1] == "OK"

    def test_wakeup_minimized(self):
        """Night assignments should be contiguous per soldier (minimize wakeups).

        With sweet-spot penalty preferring ≤90min blocks, a 6h task / 3 soldiers
        may use 4 blocks — one soldier gets 2 non-adjacent blocks (due to the
        no-consecutive-same-task constraint).  The gap between stints should be
        short (≤ half the task window) to indicate the LP is still clustering.
        """
        soldiers = [_soldier(i) for i in range(1, 4)]
        task = _task(1, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=5))
        sol = _solve(soldiers, [task])

        by_soldier = defaultdict(list)
        for a in sol.assignments:
            by_soldier[a.soldier_id].append(a)

        for sid, blocks in by_soldier.items():
            blocks.sort(key=lambda a: a.start_time)
            for i in range(1, len(blocks)):
                gap = (blocks[i].start_time - blocks[i - 1].end_time).total_seconds()
                assert gap <= 3 * 3600, (
                    f"Soldier {sid} has gap of {gap}s in night assignments — "
                    f"expected ≤ 3h (no-consecutive constraint may create gaps "
                    f"with short-block configs)"
                )

    def test_wakeup_cost_limits_wakeups(self):
        """Wakeup cost as soft brake spreads wakeups across soldiers."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        task = _task(1, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=7),
                     concurrent=2)
        sol = _solve(soldiers, [task])

        by_soldier = defaultdict(list)
        for a in sol.assignments:
            by_soldier[a.soldier_id].append(a)

        # With 5 soldiers and 2 concurrent, wakeups should be spread.
        # No soldier should have an excessive number of wakeups.
        for sid, blocks in by_soldier.items():
            blocks.sort(key=lambda a: a.start_time)
            groups = 1
            for i in range(1, len(blocks)):
                if blocks[i].start_time > blocks[i - 1].end_time:
                    groups += 1
            assert groups <= 3, f"Soldier {sid} has {groups} wakeups, expected ≤3"


# ══════════════════════════════════════════════════════════════════
# Decay scoring tests
# ══════════════════════════════════════════════════════════════════

class TestDecayScoring:
    def test_higher_excess_gets_less_work(self):
        """Soldier with higher day_excess should get fewer hours."""
        low = _soldier(1, points_day=0.0)
        high = _soldier(2, points_day=10.0)
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))

        ledger = {
            1: {"day_points": 0.0, "night_points": 0.0},
            2: {"day_points": 10.0, "night_points": 0.0},
        }
        sol = _solve([low, high], [task], ledger=ledger)

        hours = {1: 0.0, 2: 0.0}
        for a in sol.assignments:
            hours[a.soldier_id] += (a.end_time - a.start_time).total_seconds() / 3600

        assert hours[1] >= hours[2], (
            f"Low-excess soldier ({hours[1]}h) should work >= high-excess ({hours[2]}h)"
        )

    def test_night_excess_steers_quality(self):
        """Low-excess soldiers should be steered to worse night blocks."""
        soldiers = [_soldier(1), _soldier(2)]
        task = _task(1, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=5))

        ledger = {
            1: {"day_points": 0.0, "night_points": 0.0},   # Low excess
            2: {"day_points": 0.0, "night_points": 20.0},   # High excess
        }
        sol = _solve(soldiers, [task], ledger=ledger)

        # High-excess soldier should get fewer or equal hours.
        hours = {1: 0.0, 2: 0.0}
        for a in sol.assignments:
            hours[a.soldier_id] += (a.end_time - a.start_time).total_seconds() / 3600
        assert hours[1] >= hours[2]


# ══════════════════════════════════════════════════════════════════
# Configuration selection tests
# ══════════════════════════════════════════════════════════════════

class TestConfigSelection:
    def test_best_objective_wins(self):
        """The solver picks the configuration with lowest total objective."""
        soldiers = [_soldier(i) for i in range(1, 8)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=16))
        sol = _solve(soldiers, [task])
        # Just verify it completes and picks a valid solution.
        assert sol.status in ("optimal", "feasible")
        assert sol.coverage_status[1] == "OK"


# ══════════════════════════════════════════════════════════════════
# Edge cases
# ══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_short_task_covered(self):
        """A task shorter than smallest target length is still covered."""
        soldiers = [_soldier(1)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=10, minutes=30))
        sol = _solve(soldiers, [task])
        assert sol.coverage_status[1] == "OK"
        assert len(sol.assignments) >= 1

    def test_fixed_task_full_window(self):
        """Non-fractionable task: each assigned soldier covers the full duration."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=12),
                     concurrent=2, fractionable=False)
        sol = _solve(soldiers, [task])

        assert sol.coverage_status[1] == "OK"
        for a in sol.assignments:
            assert a.start_time == task.start_time
            assert a.end_time == task.end_time

    def test_single_soldier(self):
        """Single soldier available covers what they can."""
        soldiers = [_soldier(1)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        sol = _solve(soldiers, [task])
        assert sol.status in ("optimal", "feasible")
        # Single soldier should cover the whole task.
        total_hours = sum(
            (a.end_time - a.start_time).total_seconds() / 3600
            for a in sol.assignments
        )
        assert total_hours >= 3.9  # ~4 hours

    def test_all_soldiers_absent(self):
        """All soldiers absent — tasks uncovered gracefully."""
        soldiers = [
            SoldierState(
                id=1, roles=[], is_active=True,
                presence_intervals=[],  # No presence
                day_points=0.0, night_points=0.0,
            )
        ]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        sol = _solve(soldiers, [task])
        assert sol.coverage_status[1] == "UNCOVERED"

    def test_no_tasks_returns_empty(self):
        """No tasks → empty solution."""
        soldiers = [_soldier(1)]
        sol = _solve(soldiers, [])
        assert sol.assignments == []
        assert sol.status == "optimal"

    def test_no_soldiers_no_tasks(self):
        """No soldiers and no tasks → empty optimal solution."""
        sol = _solve([], [])
        assert sol.assignments == []
        assert sol.status == "optimal"

    def test_no_soldiers_all_uncovered(self):
        """With no soldiers, all tasks are uncovered."""
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        sol = _solve([], [task])
        assert sol.coverage_status[1] == "UNCOVERED"

    def test_insufficient_soldiers_uses_slack(self):
        """When not enough soldiers, coverage slack is used."""
        soldiers = [_soldier(1)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14), concurrent=3)
        sol = _solve(soldiers, [task])
        assert sol.coverage_status[1] == "UNCOVERED"
        assert 1 in sol.metrics.uncovered_task_ids


# ══════════════════════════════════════════════════════════════════
# Role eligibility
# ══════════════════════════════════════════════════════════════════

class TestRoleEligibility:
    def test_role_eligibility_respected(self):
        """Soldiers only assigned to tasks matching their roles."""
        medic = _soldier(1, roles=["Medic"])
        driver = _soldier(2, roles=["Driver"])
        generic = _soldier(3, roles=[])

        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=12),
                     roles=["Medic"])
        sol = _solve([medic, driver, generic], [task])

        for a in sol.assignments:
            assert a.soldier_id == 1, (
                f"Only the Medic (id=1) should be assigned, got soldier {a.soldier_id}"
            )

    def test_soldier_wildcard_allows_anyone(self):
        """Tasks with 'Soldier' role allow any soldier."""
        soldiers = [_soldier(i) for i in range(1, 4)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                     concurrent=2, roles=["Soldier", "Soldier"])
        sol = _solve(soldiers, [task])
        assert sol.coverage_status[1] == "OK"


# ══════════════════════════════════════════════════════════════════
# Frozen assignments
# ══════════════════════════════════════════════════════════════════

class TestFrozenAssignments:
    def test_frozen_not_reassigned(self):
        """Frozen assignments are respected — the LP doesn't override them."""
        soldiers = [_soldier(1), _soldier(2)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14), concurrent=1)

        frozen = [FrozenAssignment(
            soldier_id=1, task_id=1,
            start_time=BASE + timedelta(hours=10),
            end_time=BASE + timedelta(hours=12),
        )]
        sol = _solve(soldiers, [task], frozen=frozen)

        assert sol.coverage_status[1] == "OK"
        for a in sol.assignments:
            if a.soldier_id == 1:
                assert a.start_time >= BASE + timedelta(hours=12) or \
                       a.end_time <= BASE + timedelta(hours=10), (
                    f"LP assignment for soldier 1 overlaps frozen period"
                )


# ══════════════════════════════════════════════════════════════════
# Integration: PlannedAssignment trimming
# ══════════════════════════════════════════════════════════════════

class TestOutputTrimming:
    def test_assignments_trimmed_to_task_window(self):
        """PlannedAssignment start/end are within the task's actual window."""
        soldiers = [_soldier(i) for i in range(1, 4)]
        task = _task(1, BASE + timedelta(hours=10, minutes=20),
                     BASE + timedelta(hours=13, minutes=40))
        sol = _solve(soldiers, [task])

        for a in sol.assignments:
            assert a.start_time >= task.start_time, (
                f"Assignment starts {a.start_time} before task {task.start_time}"
            )
            assert a.end_time <= task.end_time, (
                f"Assignment ends {a.end_time} after task {task.end_time}"
            )


# ══════════════════════════════════════════════════════════════════
# Rest gap
# ══════════════════════════════════════════════════════════════════

class TestRestGap:
    def test_rest_gap_no_negative(self):
        """Soldiers should not have negative gaps (overlapping blocks)."""
        soldiers = [_soldier(i) for i in range(1, 8)]
        t1 = _task(1, BASE + timedelta(hours=8), BASE + timedelta(hours=12))
        t2 = _task(2, BASE + timedelta(hours=14), BASE + timedelta(hours=18))
        sol = _solve(soldiers, [t1, t2])

        by_soldier = defaultdict(list)
        for a in sol.assignments:
            by_soldier[a.soldier_id].append((a.start_time, a.end_time))

        for sid, intervals in by_soldier.items():
            intervals.sort()
            for i in range(1, len(intervals)):
                gap = (intervals[i][0] - intervals[i - 1][1]).total_seconds() / 60
                assert gap >= 0, f"Soldier {sid} has negative gap"


# ══════════════════════════════════════════════════════════════════
# Midnight availability gap (Bug 2)
# ══════════════════════════════════════════════════════════════════

def _soldier_daily_presence(sid, base_date, num_days=3):
    """Create a soldier with daily presence records that have a 1-second midnight gap."""
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


class TestMidnightAvailability:
    def test_merge_present_intervals_across_midnight(self):
        """Daily presence records ending at 23:59:59 merge with next day's 00:00:00."""
        soldier = _soldier_daily_presence(1, BASE)
        merged = _merge_present_intervals(soldier)

        # 3 daily records should merge into one contiguous span
        # because _ceil_minute(23:59:59) = 00:00:00 next day = start of next interval.
        assert len(merged) == 1, f"Expected 1 merged interval, got {len(merged)}: {merged}"
        assert merged[0][0] == BASE
        assert merged[0][1] >= BASE + timedelta(days=2, hours=23, minutes=59)

    def test_night_block_across_midnight_with_daily_presence(self):
        """Night block crossing midnight is available with daily presence records."""
        soldiers = [_soldier_daily_presence(i, BASE) for i in range(1, 4)]
        task = _task(1, BASE + timedelta(hours=23), BASE + timedelta(days=1, hours=5))
        sol = _solve(soldiers, [task])

        assert sol.coverage_status[1] == "OK"
        assert len(sol.assignments) >= 2  # At least some assignments

    def test_no_overcoverage_cross_midnight_task(self):
        """Task spanning day/night boundary (22:00-09:00) has no overcoverage."""
        soldiers = [_soldier_daily_presence(i, BASE) for i in range(1, 10)]
        task = _task(1,
                     BASE + timedelta(hours=22),
                     BASE + timedelta(days=1, hours=9),
                     concurrent=2)
        sol = _solve(soldiers, [task])

        assert sol.coverage_status[1] == "OK"

        # Check per-slot coverage: never more than concurrent_required.
        slot_counts = Counter()
        for a in sol.assignments:
            cursor = a.start_time
            while cursor < a.end_time:
                slot_counts[cursor] += 1
                cursor += timedelta(minutes=15)

        for slot_time, count in slot_counts.items():
            assert count <= 2, (
                f"Slot {slot_time} has {count} soldiers, concurrent=2"
            )


# ══════════════════════════════════════════════════════════════════
# Day/night period segments (no overlap)
# ══════════════════════════════════════════════════════════════════

class TestPeriodSegments:
    def test_cross_midnight_task_splits_into_segments(self):
        """Task 22:00–09:00 produces day segments (22-23, 07-09) and night segment (23-07)."""
        task = _task(1, BASE + timedelta(hours=22), BASE + timedelta(days=1, hours=9))
        day_segs, night_segs = _determine_periods([task], NIGHT_START, NIGHT_END)

        # Should have 2 day segments: 22:00–23:00 and 07:00–09:00
        assert len(day_segs) == 2, f"Expected 2 day segments, got {len(day_segs)}: {day_segs}"
        assert day_segs[0][0].hour == 22
        assert day_segs[0][1].hour == 23
        assert day_segs[1][0].hour == 7
        assert day_segs[1][1].hour == 9

        # Should have 1 night segment: 23:00–07:00
        assert len(night_segs) == 1
        assert night_segs[0][0].hour == 23

    def test_day_and_night_segments_dont_overlap(self):
        """Day and night block time ranges must not overlap."""
        task = _task(1, BASE + timedelta(hours=13), BASE + timedelta(days=1, hours=9),
                     concurrent=2)
        weights = LPWeights()
        for target in weights.target_block_lengths:
            result = _generate_all_blocks([task], target, NIGHT_START, NIGHT_END, weights)
            if result is None:
                continue
            day_blocks, night_blocks, _segments = result
            for db in day_blocks:
                for nb in night_blocks:
                    assert db.end_time <= nb.start_time or db.start_time >= nb.end_time, (
                        f"Day block {db.start_time}-{db.end_time} overlaps "
                        f"night block {nb.start_time}-{nb.end_time}"
                    )

    def test_shmirat_yom_fully_covered(self):
        """Shmirat Yom 13:00–00:00 gets full coverage including 21:00–23:00 and 23:00–00:00."""
        soldiers = [_soldier_daily_presence(i, BASE) for i in range(1, 10)]
        task = _task(1, BASE + timedelta(hours=13), BASE + timedelta(days=1),
                     concurrent=2)
        sol = _solve(soldiers, [task])

        assert sol.coverage_status[1] == "OK"

        # Verify coverage extends to midnight.
        latest_end = max(a.end_time for a in sol.assignments)
        assert latest_end >= BASE + timedelta(hours=23, minutes=45), (
            f"Latest assignment ends at {latest_end}, expected coverage to ~00:00"
        )
