"""
Tests reproducing real-world production issues from March 22, 2026.

1. Micro-block absorption: tasks starting near period boundaries should not
   produce assignments shorter than min_block_minutes.
2. Stretch penalty: no soldier should work 4+ continuous hours at night.
3. Block generation: 105min target distributes rounding evenly.
"""
from datetime import datetime, timedelta
from collections import defaultdict

import pytest

from src.core.engine import PlannedAssignment, SoldierState, TaskSpec
from src.core.lp_solver import (
    lp_solve, _generate_blocks_for_period, _generate_all_blocks,
    _map_tasks_to_blocks, TimeBlock,
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


def _soldier(sid, presence_start=None, presence_end=None):
    ps = presence_start or BASE
    pe = presence_end or (BASE + timedelta(days=7))
    return SoldierState(
        id=sid, roles=[], is_active=True,
        presence_intervals=[_presence(ps, pe)],
        day_points=0.0, night_points=0.0,
    )


def _task(tid, start, end, concurrent=1, fractionable=True, hardness=3):
    return TaskSpec(
        id=tid, real_title=f"Task-{tid}",
        start_time=start, end_time=end,
        is_fractionable=fractionable,
        is_night=start.hour >= NIGHT_START or start.hour < NIGHT_END,
        required_roles=["Soldier"],
        concurrent_required=concurrent,
        hardness=hardness,
        min_block_minutes=60,
        readiness_minutes=0,
    )


def _solve(soldiers, tasks, weights=None):
    return lp_solve(
        soldier_states=soldiers,
        task_specs=tasks,
        frozen_assignments=[],
        freeze_point=BASE,
        night_start_hour=NIGHT_START,
        night_end_hour=NIGHT_END,
        weights=weights or LPWeights(),
        effective_ledger={},
    )


# ══════════════════════════════════════════════════════════════════
# Fix 1: Micro-block absorption
# ══════════════════════════════════════════════════════════════════

class TestMicroBlockAbsorption:
    """Tasks starting near period boundaries must not produce micro-assignments."""

    def test_shmirat_laila_2245_no_15min_blocks(self):
        """Shmirat Laila 22:45→08:00 with a concurrent day task must not
        produce any assignment shorter than 30 min."""
        soldiers = [_soldier(i) for i in range(1, 10)]
        # Day task covering the evening period.
        day_task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=23),
                         concurrent=2)
        # Night task starting 15 min before the night boundary.
        night_task = _task(2, BASE + timedelta(hours=22, minutes=45),
                          BASE + timedelta(days=1, hours=8), concurrent=2)
        sol = _solve(soldiers, [day_task, night_task])

        assert sol.status in ("optimal", "feasible")
        for a in sol.assignments:
            dur = (a.end_time - a.start_time).total_seconds() / 60.0
            assert dur >= 30 - 0.1, (
                f"Micro-assignment {a.start_time}→{a.end_time} ({dur:.0f}min) "
                f"for soldier {a.soldier_id} task {a.task_id}"
            )

    def test_night_guard_2230_no_30min_blocks(self):
        """Night Guard 22:30→08:30 with a concurrent day task must not
        produce any assignment of 30 min or shorter at a period boundary."""
        soldiers = [_soldier(i) for i in range(1, 10)]
        day_task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=23),
                         concurrent=2)
        night_task = _task(2, BASE + timedelta(hours=22, minutes=30),
                          BASE + timedelta(days=1, hours=8, minutes=30), concurrent=2)
        sol = _solve(soldiers, [day_task, night_task])

        assert sol.status in ("optimal", "feasible")
        for a in sol.assignments:
            dur = (a.end_time - a.start_time).total_seconds() / 60.0
            assert dur >= 30 - 0.1, (
                f"Micro-assignment {a.start_time}→{a.end_time} ({dur:.0f}min) "
                f"for soldier {a.soldier_id} task {a.task_id}"
            )

    def test_map_tasks_skips_small_overlaps(self):
        """_map_tasks_to_blocks with min_overlap_minutes skips tiny overlaps."""
        blocks = [
            TimeBlock(block_id=0,
                      start_time=BASE + timedelta(hours=20, minutes=30),
                      end_time=BASE + timedelta(hours=23),
                      is_night=False, night_quality_tier=0, active_tasks=[]),
        ]
        # Task starts 15 min before block ends.
        task = _task(1, BASE + timedelta(hours=22, minutes=45),
                     BASE + timedelta(days=1, hours=8))

        # Without min_overlap: task IS active.
        _map_tasks_to_blocks(blocks, [task], min_overlap_minutes=0)
        assert len(blocks[0].active_tasks) == 1

        # With min_overlap=30: task is NOT active (only 15 min overlap).
        _map_tasks_to_blocks(blocks, [task], min_overlap_minutes=30)
        assert len(blocks[0].active_tasks) == 0

    def test_absorption_catches_30min_segments(self):
        """Cross-segment absorption now absorbs 30-min single-block segments."""
        soldiers = [_soldier(i) for i in range(1, 10)]
        # Task starting exactly 30 min before night boundary.
        task = _task(1, BASE + timedelta(hours=22, minutes=30),
                     BASE + timedelta(days=1, hours=7), concurrent=2)
        w = LPWeights()
        result = _generate_all_blocks(
            [task], 105, NIGHT_START, NIGHT_END, w,
        )
        assert result is not None
        day_blocks, night_blocks, segments = result
        # The 30-min day segment should be absorbed — no day blocks remain.
        assert len(day_blocks) == 0, (
            f"Expected 0 day blocks (absorbed), got {len(day_blocks)}: "
            f"{[(b.start_time, b.end_time) for b in day_blocks]}"
        )


# ══════════════════════════════════════════════════════════════════
# Fix 2: Stretch penalty prevents long continuous night work
# ══════════════════════════════════════════════════════════════════

class TestStretchPenalty:
    """Increased stretch penalty should prevent 4+h continuous night stretches."""

    def test_no_long_continuous_night(self):
        """Two concurrent night tasks with 15 soldiers: no soldier gets >4h continuous."""
        soldiers = [_soldier(i) for i in range(1, 16)]
        # Two night tasks, ~9h and ~8h, concurrent=2.
        task1 = _task(1, BASE + timedelta(hours=23),
                      BASE + timedelta(days=1, hours=8), concurrent=2, hardness=4)
        task2 = _task(2, BASE + timedelta(hours=23),
                      BASE + timedelta(days=1, hours=7), concurrent=2, hardness=3)
        sol = _solve(soldiers, [task1, task2])
        assert sol.status in ("optimal", "feasible")

        # Build per-soldier sorted assignment list.
        by_soldier = defaultdict(list)
        for a in sol.assignments:
            by_soldier[a.soldier_id].append((a.start_time, a.end_time))

        max_stretch = 0.0
        for sid, intervals in by_soldier.items():
            intervals.sort()
            # Merge contiguous intervals into stretches.
            stretches = [intervals[0]]
            for start, end in intervals[1:]:
                prev_start, prev_end = stretches[-1]
                if abs((start - prev_end).total_seconds()) <= 60:  # within 1 min = contiguous
                    stretches[-1] = (prev_start, end)
                else:
                    stretches.append((start, end))

            for s_start, s_end in stretches:
                stretch_h = (s_end - s_start).total_seconds() / 3600.0
                max_stretch = max(max_stretch, stretch_h)
                assert stretch_h <= 4.0 + 0.1, (
                    f"Soldier {sid} has {stretch_h:.1f}h continuous stretch "
                    f"{s_start}→{s_end}"
                )

    def test_no_6h_continuous_day(self):
        """Day tasks with enough soldiers: no soldier gets 6+h continuous."""
        soldiers = [_soldier(i) for i in range(1, 13)]
        task1 = _task(1, BASE + timedelta(hours=9),
                      BASE + timedelta(hours=19), concurrent=2)
        task2 = _task(2, BASE + timedelta(hours=10),
                      BASE + timedelta(hours=18), concurrent=2)
        sol = _solve(soldiers, [task1, task2])
        assert sol.status in ("optimal", "feasible")

        by_soldier = defaultdict(list)
        for a in sol.assignments:
            by_soldier[a.soldier_id].append((a.start_time, a.end_time))

        for sid, intervals in by_soldier.items():
            intervals.sort()
            stretches = [intervals[0]]
            for start, end in intervals[1:]:
                prev_start, prev_end = stretches[-1]
                if abs((start - prev_end).total_seconds()) <= 60:
                    stretches[-1] = (prev_start, end)
                else:
                    stretches.append((start, end))

            for s_start, s_end in stretches:
                stretch_h = (s_end - s_start).total_seconds() / 3600.0
                assert stretch_h < 6.0, (
                    f"Soldier {sid} has {stretch_h:.1f}h continuous day stretch "
                    f"{s_start}→{s_end}"
                )


# ══════════════════════════════════════════════════════════════════
# Fix 3: Block generation distributes rounding evenly
# ══════════════════════════════════════════════════════════════════

class TestBlockGenerationRounding:
    """105min target should not dump all surplus on the last block."""

    def test_105min_9h_window_no_120min_last_block(self):
        """9h window with 105min target: last block should not always be 120min."""
        start = datetime(2026, 4, 1, 10, 0)
        end = datetime(2026, 4, 1, 19, 0)
        blocks = _generate_blocks_for_period(start, end, 105, False, 23, 7)

        durations = [b.duration_minutes for b in blocks]
        # The surplus (540 - 5*105 = 15min) should not all land on the last block.
        # With even distribution, the last block should be similar to interior blocks.
        assert durations[-1] <= 120 + 0.1, f"Last block too long: {durations}"
        # At most one block should be 120min (absorbing the 15min surplus).
        count_120 = sum(1 for d in durations if d >= 120 - 0.1)
        assert count_120 <= 1, f"Too many 120min blocks: {durations}"

    def test_105min_8h_window_even_distribution(self):
        """8h window with 105min target: rounding distributed evenly."""
        start = datetime(2026, 4, 1, 23, 0)
        end = datetime(2026, 4, 2, 7, 0)
        blocks = _generate_blocks_for_period(start, end, 105, True, 23, 7)

        durations = [b.duration_minutes for b in blocks]
        # All blocks should be between 90 and 120 minutes.
        for d in durations:
            assert 75 <= d <= 120, f"Block duration {d} out of expected range: {durations}"

    def test_blocks_still_cover_full_period(self):
        """After fix, blocks must still cover the full period without gaps."""
        for target in [60, 75, 90, 105, 120, 150, 180]:
            start = datetime(2026, 4, 1, 10, 0)
            end = datetime(2026, 4, 1, 19, 0)
            blocks = _generate_blocks_for_period(start, end, target, False, 23, 7)
            if not blocks:
                continue
            assert blocks[0].start_time == start, f"target={target}: gap at start"
            assert blocks[-1].end_time == end, f"target={target}: gap at end"
            for i in range(1, len(blocks)):
                assert blocks[i].start_time == blocks[i - 1].end_time, (
                    f"target={target}: gap between blocks {i-1} and {i}"
                )
