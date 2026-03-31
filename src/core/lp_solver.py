"""
Two-stage block-based LP schedule solver using PuLP + CBC.

Architecture:
  Period Block Generation (arithmetic) → Day LP Solve → Night LP Solve
  → Pick best across configurations

See docs/LP_FORMULATION.md for the full mathematical specification.
"""
import logging
import sys
import time
from collections import defaultdict as _dd
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

import pulp

from src.core.engine import (
    FrozenAssignment,
    PlannedAssignment,
    SoldierState,
    TaskSpec,
    _ceil_minute,
)
from src.core.lp_weights import LPWeights
from src.domain.command_rules import resolve_active_commander


GRID_MINUTES = 15  # All boundaries snap to this grid.


@dataclass
class SolutionMetrics:
    """Summary statistics for the UI."""
    avg_day_deviation: float = 0.0
    max_day_deviation: float = 0.0
    avg_night_deviation: float = 0.0
    max_night_deviation: float = 0.0
    total_night_restarts: int = 0
    total_day_starts: int = 0
    shortest_gap_minutes: float = float("inf")
    uncovered_task_ids: list = field(default_factory=list)


@dataclass
class LPSolution:
    assignments: list  # list[PlannedAssignment]
    coverage_status: dict  # task_id -> 'OK' | 'UNCOVERED'
    metrics: SolutionMetrics
    solve_time_seconds: float
    status: str  # 'optimal' | 'feasible' | 'infeasible'


def _log(msg: str) -> None:
    print(f"[LP] {msg}", file=sys.stderr)


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _snap_to_grid(dt: datetime) -> datetime:
    """Snap a datetime to the nearest 15-minute boundary (round down)."""
    m = (dt.minute // GRID_MINUTES) * GRID_MINUTES
    return dt.replace(minute=m, second=0, microsecond=0)


def _snap_to_nearest_grid(dt: datetime) -> datetime:
    """Snap a datetime to the nearest 15-minute boundary (round to nearest)."""
    remainder = dt.minute % GRID_MINUTES
    if remainder <= GRID_MINUTES / 2:
        return dt.replace(minute=(dt.minute // GRID_MINUTES) * GRID_MINUTES, second=0, microsecond=0)
    else:
        up_min = ((dt.minute // GRID_MINUTES) + 1) * GRID_MINUTES
        if up_min >= 60:
            return (dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        return dt.replace(minute=up_min, second=0, microsecond=0)


def _snap_up_to_grid(dt: datetime) -> datetime:
    """Snap a datetime to the nearest 15-minute boundary (round up)."""
    if dt.second or dt.microsecond or (dt.minute % GRID_MINUTES):
        m = ((dt.minute + GRID_MINUTES - 1) // GRID_MINUTES) * GRID_MINUTES
        if m >= 60:
            return (dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        return dt.replace(minute=m, second=0, microsecond=0)
    return dt.replace(second=0, microsecond=0)


def _classify_night_tier(
    block_midpoint: datetime,
    night_start_hour: int,
    night_end_hour: int,
) -> int:
    """Compute night quality tier for a block midpoint within the night window.

    First quarter = tier 1 (best), last quarter = tier 2, middle two quarters = tier 3 (worst).
    """
    if night_end_hour <= night_start_hour:
        night_len_h = (24 - night_start_hour) + night_end_hour
    else:
        night_len_h = night_end_hour - night_start_hour
    quarter_h = night_len_h / 4.0

    h = block_midpoint.hour + block_midpoint.minute / 60.0
    if h >= night_start_hour:
        hours_in = h - night_start_hour
    else:
        hours_in = (24 - night_start_hour) + h

    if hours_in < quarter_h:
        return 1
    elif hours_in >= night_len_h - quarter_h:
        return 2
    else:
        return 3


def _is_night_time(dt: datetime, night_start_hour: int, night_end_hour: int) -> bool:
    """Check if a datetime falls within the night window."""
    h = dt.hour + dt.minute / 60.0
    if night_end_hour <= night_start_hour:
        return h >= night_start_hour or h < night_end_hour
    else:
        return night_start_hour <= h < night_end_hour


# ──────────────────────────────────────────────────────────────────
# Stepped rest gap penalty functions
# ──────────────────────────────────────────────────────────────────


def _night_gap_penalty_factor(gap_hours: float) -> float:
    """Stepped penalty factor for night→night rest gaps.

    Steeper curve — disrupting sleep mid-cycle is the most harmful pattern.

    Gap ≤ 1h:  12.0  (very heavy)
    Gap 1–3h:   8.0  (heavy)
    Gap 3–5h:   4.0  (moderate)
    Gap 5–6h:   1.5  (light)
    Gap > 6h:   0.0  (no penalty)
    """
    if gap_hours > 6.0:
        return 0.0
    if gap_hours > 5.0:
        return 1.5
    if gap_hours > 3.0:
        return 4.0
    if gap_hours > 1.0:
        return 8.0
    return 12.0


def _day_gap_penalty_factor(gap_hours: float) -> float:
    """Stepped penalty factor for day→day and cross-domain rest gaps.

    Lighter curve — soldier is awake or transitioning, less harmful than
    mid-sleep disruption.

    Gap ≤ 1h:  10.0  (very heavy)
    Gap 1–3h:   5.0  (moderate)
    Gap 3–5h:   2.0  (light)
    Gap > 5h:   0.0  (no penalty)
    """
    if gap_hours > 5.0:
        return 0.0
    if gap_hours > 3.0:
        return 2.0
    if gap_hours > 1.0:
        return 5.0
    return 10.0


# ──────────────────────────────────────────────────────────────────
# Block data structures
# ──────────────────────────────────────────────────────────────────

@dataclass
class TimeBlock:
    block_id: int
    start_time: datetime
    end_time: datetime
    is_night: bool
    night_quality_tier: int  # 0 for day blocks
    active_tasks: list  # list of (task_id, concurrent_required)
    duration_minutes: float = 0.0

    def __post_init__(self):
        self.duration_minutes = (self.end_time - self.start_time).total_seconds() / 60.0


@dataclass
class ConfigResult:
    """Result of one block-length configuration."""
    target_length: int
    day_blocks: list  # list[TimeBlock]
    night_blocks: list  # list[TimeBlock]
    day_assignments: list  # list[PlannedAssignment]
    night_assignments: list  # list[PlannedAssignment]
    day_objective: float
    night_objective: float
    total_objective: float
    day_coverage_status: dict  # task_id -> 'OK' | 'UNCOVERED'
    night_coverage_status: dict  # task_id -> 'OK' | 'UNCOVERED'
    day_status: str
    night_status: str
    metrics: SolutionMetrics


@dataclass
class PeriodSegment:
    """A contiguous time segment belonging to either day or night."""
    seg_type: str   # 'day' or 'night'
    start_time: datetime
    end_time: datetime
    blocks: list  # list[TimeBlock] — references into the flat day/night block lists


# ──────────────────────────────────────────────────────────────────
# Stage 0: Determine day and night periods
# ──────────────────────────────────────────────────────────────────

def _determine_periods(
    task_specs: list[TaskSpec],
    night_start_hour: int,
    night_end_hour: int,
) -> tuple[list[tuple[datetime, datetime]], list[tuple[datetime, datetime]]]:
    """Determine contiguous day and night segments from active tasks.

    Returns (day_segments, night_segments) where each is a list of
    (start, end) tuples representing contiguous periods.

    A task spanning day and night (e.g. 22:00–09:00) produces separate
    segments: a day segment 22:00–23:00 and a night segment 23:00–07:00
    and a day segment 07:00–09:00.  These never overlap.
    """
    if not task_specs:
        return [], []

    # Collect all 15-min slots classified as day or night.
    day_slots: list[datetime] = []
    night_slots: list[datetime] = []

    for spec in task_specs:
        if not spec.is_fractionable:
            continue

        cursor = _snap_to_grid(spec.start_time)
        task_end = _snap_up_to_grid(spec.end_time)

        while cursor < task_end:
            mid = cursor + timedelta(minutes=GRID_MINUTES / 2)
            if _is_night_time(mid, night_start_hour, night_end_hour):
                night_slots.append(cursor)
            else:
                day_slots.append(cursor)
            cursor += timedelta(minutes=GRID_MINUTES)

    def _merge_to_segments(slots: list[datetime]) -> list[tuple[datetime, datetime]]:
        """Merge sorted slots into contiguous segments."""
        if not slots:
            return []
        unique = sorted(set(slots))
        segments = []
        seg_start = unique[0]
        seg_end = unique[0] + timedelta(minutes=GRID_MINUTES)
        for slot in unique[1:]:
            slot_end = slot + timedelta(minutes=GRID_MINUTES)
            if slot <= seg_end:
                seg_end = max(seg_end, slot_end)
            else:
                segments.append((seg_start, seg_end))
                seg_start = slot
                seg_end = slot_end
        segments.append((seg_start, seg_end))
        return segments

    return _merge_to_segments(day_slots), _merge_to_segments(night_slots)


# ──────────────────────────────────────────────────────────────────
# Stage 1: Block generation
# ──────────────────────────────────────────────────────────────────

def _generate_blocks_for_period(
    period_start: datetime,
    period_end: datetime,
    target_length_minutes: int,
    is_night: bool,
    night_start_hour: int,
    night_end_hour: int,
    start_block_id: int = 0,
) -> list[TimeBlock]:
    """Divide a period into uniform blocks of approximately target_length_minutes."""
    period_duration = (period_end - period_start).total_seconds() / 60.0
    if period_duration <= 0:
        return []

    num_blocks = max(1, round(period_duration / target_length_minutes))
    # Ensure blocks don't exceed max duration (3h = 180min by default).
    # If rounding down gave too-long blocks, use one more block.
    max_block = 180  # Will be validated by caller anyway.
    if period_duration / num_blocks > max_block:
        num_blocks = max(1, -(-int(period_duration) // max_block))  # ceil division
    base_duration = period_duration / num_blocks

    # Pre-compute all block boundaries from the period start.
    # Each boundary is computed independently as round(i * duration / N)
    # snapped to the nearest grid point.  This distributes rounding error
    # evenly across blocks instead of accumulating it on the last block.
    boundaries = [period_start]
    for i in range(1, num_blocks):
        raw_minutes = i * period_duration / num_blocks
        boundary = period_start + timedelta(minutes=raw_minutes)
        boundary = _snap_to_nearest_grid(boundary)
        # Ensure monotonicity.
        if boundary <= boundaries[-1]:
            boundary = boundaries[-1] + timedelta(minutes=GRID_MINUTES)
        if boundary > period_end:
            boundary = period_end
        boundaries.append(boundary)
    boundaries.append(period_end)

    blocks = []
    for i in range(num_blocks):
        cursor = boundaries[i]
        block_end = boundaries[i + 1]

        if block_end <= cursor:
            continue

        midpoint = cursor + (block_end - cursor) / 2
        tier = 0
        if is_night:
            tier = _classify_night_tier(midpoint, night_start_hour, night_end_hour)

        blocks.append(TimeBlock(
            block_id=start_block_id + i,
            start_time=cursor,
            end_time=block_end,
            is_night=is_night,
            night_quality_tier=tier,
            active_tasks=[],  # Filled in later.
        ))
        cursor = block_end

    return blocks


def _map_tasks_to_blocks(
    blocks: list[TimeBlock],
    task_specs: list[TaskSpec],
    min_overlap_minutes: int = 0,
) -> None:
    """For each block, determine which tasks are active and set active_tasks.

    If min_overlap_minutes > 0, tasks whose overlap with a block is shorter
    than this threshold are excluded.  This prevents micro-assignments at
    period boundaries (e.g. a task starting at 22:45 would only overlap a
    day block ending at 23:00 by 15 min — too short to be useful).
    """
    for block in blocks:
        active = []
        for spec in task_specs:
            if not spec.is_fractionable:
                continue  # Fixed tasks handled separately.
            # Task is active in block if their time windows overlap.
            if spec.end_time > block.start_time and spec.start_time < block.end_time:
                # Check minimum overlap: the trimmed assignment duration
                # must be at least min_overlap_minutes.
                if min_overlap_minutes > 0:
                    overlap_start = max(block.start_time, spec.start_time)
                    overlap_end = min(block.end_time, spec.end_time)
                    overlap_min = (overlap_end - overlap_start).total_seconds() / 60.0
                    if overlap_min < min_overlap_minutes - 0.1:
                        continue
                active.append((spec.id, spec.concurrent_required))
        block.active_tasks = active


def _validate_blocks(
    blocks: list[TimeBlock],
    min_minutes: int,
    max_minutes: int,
) -> bool:
    """Check that all generated blocks are within the allowed size range.

    Exception: if there's only 1 block and the entire period is shorter than
    min_minutes, the block is valid (it covers the whole period).
    """
    for block in blocks:
        dur = block.duration_minutes
        if dur > max_minutes + 0.1:
            return False
        if dur < min_minutes - 0.1:
            # Allow a single block that covers the entire (short) period.
            if len(blocks) == 1:
                continue
            return False
    return True


def _absorb_micro_blocks(
    blocks: list[TimeBlock],
    min_minutes: int = 30,
) -> list[TimeBlock]:
    """Absorb edge blocks at or below min_minutes into their neighbor.

    Only affects the first and last block of a segment.  Interior blocks
    are already > min_minutes from block generation.  The ``<=`` threshold
    ensures that blocks of exactly min_minutes at period boundaries are
    also absorbed (e.g. a 30-min day block from a task starting at 22:30
    with night at 23:00).
    """
    if len(blocks) < 2:
        return blocks
    # First block too short → extend second block's start earlier.
    if blocks[0].duration_minutes <= min_minutes + 0.1:
        b = blocks[1]
        blocks = [TimeBlock(
            block_id=b.block_id,
            start_time=blocks[0].start_time,
            end_time=b.end_time,
            is_night=b.is_night,
            night_quality_tier=b.night_quality_tier,
            active_tasks=[],
        )] + blocks[2:]
    # Last block too short → extend second-to-last block's end later.
    if len(blocks) >= 2 and blocks[-1].duration_minutes <= min_minutes + 0.1:
        b = blocks[-2]
        blocks = blocks[:-2] + [TimeBlock(
            block_id=b.block_id,
            start_time=b.start_time,
            end_time=blocks[-1].end_time,
            is_night=b.is_night,
            night_quality_tier=b.night_quality_tier,
            active_tasks=[],
        )]
    return blocks


def _generate_all_blocks(
    task_specs: list[TaskSpec],
    target_length: int,
    night_start_hour: int,
    night_end_hour: int,
    weights: LPWeights,
) -> Optional[tuple[list[TimeBlock], list[TimeBlock], list[PeriodSegment]]]:
    """Generate day and night blocks for a given target length.

    Returns (day_blocks, night_blocks, segments) or None if the configuration
    is invalid.  ``segments`` is a list of PeriodSegment sorted chronologically
    by start time; each segment owns a subset of the flat day/night block lists.
    Day and night segments never overlap — a task spanning both (e.g. 22:00–09:00)
    is split at the night boundary.
    """
    day_segments, night_segments = _determine_periods(task_specs, night_start_hour, night_end_hour)

    day_blocks: list[TimeBlock] = []
    night_blocks: list[TimeBlock] = []
    segments: list[PeriodSegment] = []
    next_id = 0

    for seg_start, seg_end in day_segments:
        seg_blocks = _generate_blocks_for_period(
            seg_start, seg_end, target_length,
            is_night=False,
            night_start_hour=night_start_hour,
            night_end_hour=night_end_hour,
            start_block_id=next_id,
        )
        n_generated = len(seg_blocks)
        seg_blocks = _absorb_micro_blocks(seg_blocks, weights.min_block_minutes)
        _map_tasks_to_blocks(seg_blocks, task_specs, min_overlap_minutes=weights.min_block_minutes)
        if not _validate_blocks(seg_blocks, weights.min_block_minutes, weights.max_block_minutes):
            return None
        day_blocks.extend(seg_blocks)
        segments.append(PeriodSegment(
            seg_type='day', start_time=seg_start, end_time=seg_end, blocks=seg_blocks,
        ))
        next_id += n_generated

    for seg_start, seg_end in night_segments:
        seg_blocks = _generate_blocks_for_period(
            seg_start, seg_end, target_length,
            is_night=True,
            night_start_hour=night_start_hour,
            night_end_hour=night_end_hour,
            start_block_id=next_id,
        )
        n_generated = len(seg_blocks)
        seg_blocks = _absorb_micro_blocks(seg_blocks, weights.min_block_minutes)
        _map_tasks_to_blocks(seg_blocks, task_specs, min_overlap_minutes=weights.min_block_minutes)
        if not _validate_blocks(seg_blocks, weights.min_block_minutes, weights.max_block_minutes):
            return None
        night_blocks.extend(seg_blocks)
        segments.append(PeriodSegment(
            seg_type='night', start_time=seg_start, end_time=seg_end, blocks=seg_blocks,
        ))
        next_id += n_generated

    # Sort segments chronologically for the solve loop.
    segments.sort(key=lambda s: s.start_time)

    # ── Absorb micro-segments at period boundaries ──
    # A single-block segment shorter than min_block_minutes (e.g. a 15-min
    # day block before the night boundary, or a 30-min edge segment) creates
    # pointless micro-assignments.  Absorb into adjacent segment's edge block.
    _min = weights.min_block_minutes
    absorbed_any = False
    to_remove: set[int] = set()
    for i, seg in enumerate(segments):
        if len(seg.blocks) != 1 or seg.blocks[0].duration_minutes > _min + 0.1:
            continue
        micro = seg.blocks[0]
        absorbed = False
        # Try next segment's first block.
        if i + 1 < len(segments) and i + 1 not in to_remove:
            next_seg = segments[i + 1]
            if next_seg.blocks:
                nb = next_seg.blocks[0]
                next_seg.blocks[0] = TimeBlock(
                    block_id=nb.block_id,
                    start_time=micro.start_time,
                    end_time=nb.end_time,
                    is_night=nb.is_night,
                    night_quality_tier=nb.night_quality_tier,
                    active_tasks=[],
                )
                _map_tasks_to_blocks([next_seg.blocks[0]], task_specs, min_overlap_minutes=_min)
                absorbed = True
        # Fallback: try previous segment's last block.
        if not absorbed and i > 0 and i - 1 not in to_remove:
            prev_seg = segments[i - 1]
            if prev_seg.blocks:
                pb = prev_seg.blocks[-1]
                prev_seg.blocks[-1] = TimeBlock(
                    block_id=pb.block_id,
                    start_time=pb.start_time,
                    end_time=micro.end_time,
                    is_night=pb.is_night,
                    night_quality_tier=pb.night_quality_tier,
                    active_tasks=[],
                )
                _map_tasks_to_blocks([prev_seg.blocks[-1]], task_specs, min_overlap_minutes=_min)
                absorbed = True
        if absorbed:
            to_remove.add(i)
            absorbed_any = True

    if absorbed_any:
        segments = [s for i, s in enumerate(segments) if i not in to_remove]
        day_blocks = [b for s in segments if s.seg_type == 'day' for b in s.blocks]
        night_blocks = [b for s in segments if s.seg_type == 'night' for b in s.blocks]
        # Re-validate after absorption (merged block may exceed max).
        if not _validate_blocks(day_blocks, weights.min_block_minutes, weights.max_block_minutes):
            return None
        if not _validate_blocks(night_blocks, weights.min_block_minutes, weights.max_block_minutes):
            return None

    return day_blocks, night_blocks, segments


# ──────────────────────────────────────────────────────────────────
# Availability and eligibility
# ──────────────────────────────────────────────────────────────────

def _build_eligible_matrix(
    soldier_states: list[SoldierState],
    task_specs: list[TaskSpec],
) -> dict[int, dict[int, bool]]:
    """eligible[soldier_id][task_id] = True if soldier has qualifying role
    and is not in the task's exclusion list."""
    eligible: dict[int, dict[int, bool]] = {}
    for state in soldier_states:
        task_elig: dict[int, bool] = {}
        for spec in task_specs:
            # Check per-task exclusion list.
            excluded = getattr(spec, "excluded_soldier_ids", None) or []
            if state.id in excluded:
                task_elig[spec.id] = False
                continue
            if not spec.required_roles or "Soldier" in spec.required_roles:
                task_elig[spec.id] = True
            else:
                task_elig[spec.id] = any(r in state.roles for r in spec.required_roles)
        eligible[state.id] = task_elig
    return eligible


def _build_soldier_presence_map(
    soldier_states: list[SoldierState],
) -> dict[int, list[tuple[datetime, datetime]]]:
    """Build a soldier_id → merged presence intervals map for commander resolution."""
    return {s.id: _merge_present_intervals(s) for s in soldier_states}


def _merge_present_intervals(
    state: SoldierState,
) -> list[tuple[datetime, datetime]]:
    """Merge consecutive PRESENT intervals into continuous spans.

    Handles the midnight gap: daily records end at 23:59:59 and the next starts
    at 00:00:00.  _ceil_minute rounds 23:59:59 → 00:00:00 next day, so these
    intervals are treated as contiguous.
    """
    raw = sorted(
        (
            (pi.start_time, _ceil_minute(pi.end_time))
            for pi in state.presence_intervals
            if getattr(pi, "status", None) == "PRESENT"
        ),
        key=lambda x: x[0],
    )
    if not raw:
        return []

    merged: list[tuple[datetime, datetime]] = [raw[0]]
    for start, end in raw[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            # Overlapping or contiguous — merge.
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _compute_block_overlap_minutes(
    state: SoldierState,
    block: TimeBlock,
) -> float:
    """Compute overlap in minutes between soldier's PRESENT intervals and a block."""
    merged = _merge_present_intervals(state)
    total = 0.0
    for pi_start, pi_end in merged:
        ov_start = max(pi_start, block.start_time)
        ov_end = min(pi_end, block.end_time)
        if ov_end > ov_start:
            total += (ov_end - ov_start).total_seconds() / 60.0
    return total


def _soldier_available_for_block(
    state: SoldierState,
    block: TimeBlock,
    frozen_assignments: list[FrozenAssignment],
) -> bool:
    """Check if soldier is present for enough of the block and not frozen.

    Allows up to one grid step (15min) gap at the block boundary, but only
    when the block is long enough that 15min is a small fraction (>= 90min).
    Shorter blocks require full coverage.
    Uses merged presence intervals to handle the midnight gap.
    """
    if not state.is_active:
        return False

    overlap = _compute_block_overlap_minutes(state, block)
    # For blocks >= 90min, allow up to 15min gap at boundary.
    # For shorter blocks, require full coverage.
    if block.duration_minutes >= 90:
        threshold = block.duration_minutes - GRID_MINUTES
    else:
        threshold = block.duration_minutes
    if overlap < threshold - 0.1:
        return False

    # Check no frozen assignment overlaps.
    for fa in frozen_assignments:
        if fa.soldier_id == state.id:
            if fa.end_time > block.start_time and fa.start_time < block.end_time:
                return False

    return True


# ──────────────────────────────────────────────────────────────────
# Stage 2a: Day LP Solve
# ──────────────────────────────────────────────────────────────────

def _solve_day_lp(
    day_blocks: list[TimeBlock],
    soldier_states: list[SoldierState],
    task_specs: list[TaskSpec],
    frozen_assignments: list[FrozenAssignment],
    day_excess: dict[int, float],
    weights: LPWeights,
    night_start_hour: int,
    night_end_hour: int,
    prior_assignments: list[PlannedAssignment] | None = None,
    prior_segment_hours: dict[int, float] | None = None,
    command_chain: list[int] | None = None,
    soldier_presence: dict[int, list[tuple[datetime, datetime]]] | None = None,
) -> tuple[list[PlannedAssignment], dict[int, str], float, str, dict[int, float]]:
    """Solve the day LP.

    Args:
        prior_assignments: Assignments from previously-solved segments (any domain).
            Used for cross-segment rest gap penalties.
        prior_segment_hours: Accumulated raw hours per soldier from previously-solved
            day segments within this lp_solve() call.  Feeds into fairness.

    Returns (assignments, coverage_status, objective_value, status, soldier_hours).
    soldier_hours maps soldier_id to raw hours assigned in this segment.
    """
    if prior_assignments is None:
        prior_assignments = []
    if prior_segment_hours is None:
        prior_segment_hours = {}

    if not day_blocks:
        return [], {}, 0.0, "optimal", {}

    spec_by_id = {s.id: s for s in task_specs}
    eligible = _build_eligible_matrix(soldier_states, task_specs)
    states_by_id = {s.id: s for s in soldier_states}
    soldier_ids = [s.id for s in soldier_states if s.is_active]

    # Compute frozen coverage per (task, block).
    frozen_cov: dict[tuple[int, int], int] = {}  # (task_id, block_id) -> count
    for fa in frozen_assignments:
        for block in day_blocks:
            for tid, _ in block.active_tasks:
                if fa.task_id == tid and fa.end_time > block.start_time and fa.start_time < block.end_time:
                    key = (tid, block.block_id)
                    frozen_cov[key] = frozen_cov.get(key, 0) + 1

    # Determine which (soldier, task, block) triples are possible.
    _cmd_chain = command_chain or []
    _sol_pres = soldier_presence or {}
    possible: set[tuple[int, int, int]] = set()  # (sid, tid, block_id)
    for sid in soldier_ids:
        state = states_by_id[sid]
        for block in day_blocks:
            if not _soldier_available_for_block(state, block, frozen_assignments):
                continue
            for tid, _ in block.active_tasks:
                if not eligible.get(sid, {}).get(tid, False):
                    continue
                # Commander exclusion: per-block check at block midpoint.
                spec = spec_by_id.get(tid)
                if spec and not spec.include_commander and _cmd_chain:
                    mid = block.start_time + (block.end_time - block.start_time) / 2
                    cmdr = resolve_active_commander(_cmd_chain, _sol_pres, mid)
                    if cmdr == sid:
                        continue
                possible.add((sid, tid, block.block_id))

    # Log eligible soldiers per (task, block).
    for block in day_blocks:
        for tid, conc in block.active_tasks:
            elig_count = sum(1 for sid in soldier_ids if (sid, tid, block.block_id) in possible)
            fc = frozen_cov.get((tid, block.block_id), 0)
            remaining = max(conc - fc, 0)
            task_name = spec_by_id.get(tid, type('', (), {'real_title': f'T{tid}'})()).real_title
            if elig_count <= remaining + 2:  # Only log constrained blocks.
                _log(f"    block {block.block_id} [{block.start_time.strftime('%H:%M')}-"
                     f"{block.end_time.strftime('%H:%M')}] {task_name}: "
                     f"{elig_count} eligible, {remaining} needed (frozen={fc})")

    if not possible:
        # No possible assignments — all tasks uncovered.
        cov = {}
        for block in day_blocks:
            for tid, conc in block.active_tasks:
                fc = frozen_cov.get((tid, block.block_id), 0)
                if fc < conc:
                    cov[tid] = "UNCOVERED"
                elif tid not in cov:
                    cov[tid] = "OK"
        return [], cov, 0.0, "optimal", {}

    # Build model.
    model = pulp.LpProblem("DayLP", pulp.LpMinimize)

    # y[s,t,b] binary variables.
    y = {}
    for (sid, tid, bid) in possible:
        y[(sid, tid, bid)] = pulp.LpVariable(f"yd_{sid}_{tid}_{bid}", cat="Binary")

    # Slack variables for coverage.
    slack = {}
    for block in day_blocks:
        for tid, conc in block.active_tasks:
            slack[(tid, block.block_id)] = pulp.LpVariable(
                f"sd_{tid}_{block.block_id}", lowBound=0,
            )

    # ── Constraints ──

    # Coverage: for each (task, block), sum of assigned soldiers = concurrent_required.
    for block in day_blocks:
        for tid, conc in block.active_tasks:
            assigned = pulp.lpSum(
                y[(sid, tid, block.block_id)]
                for sid in soldier_ids
                if (sid, tid, block.block_id) in y
            )
            fc = frozen_cov.get((tid, block.block_id), 0)
            remaining = max(conc - fc, 0)
            model += (
                assigned + slack[(tid, block.block_id)] >= remaining,
                f"cov_{tid}_{block.block_id}",
            )
            # Upper bound: no overcoverage.
            model += (
                assigned <= remaining,
                f"covub_{tid}_{block.block_id}",
            )

    # No overlap: soldier covers at most one task per block.
    for sid in soldier_ids:
        for block in day_blocks:
            tasks_in_block = [
                y[(sid, tid, block.block_id)]
                for tid, _ in block.active_tasks
                if (sid, tid, block.block_id) in y
            ]
            if len(tasks_in_block) > 1:
                model += (
                    pulp.lpSum(tasks_in_block) <= 1,
                    f"nooverlap_{sid}_{block.block_id}",
                )

    # ── Fairness variables ──
    block_by_id = {b.block_id: b for b in day_blocks}

    # Remaining LP demand (after frozen coverage).
    total_day_demand = sum(
        max(conc - frozen_cov.get((tid, block.block_id), 0), 0)
        * block.duration_minutes / 60.0
        for block in day_blocks
        for tid, conc in block.active_tasks
    )

    # Prior (frozen/fixed) hours per soldier within the day block period.
    prior_day_hours: dict[int, float] = {}
    for fa in frozen_assignments:
        for block in day_blocks:
            ov_start = max(fa.start_time, block.start_time)
            ov_end = min(fa.end_time, block.end_time)
            if ov_end > ov_start:
                h = (ov_end - ov_start).total_seconds() / 3600.0
                prior_day_hours[fa.soldier_id] = prior_day_hours.get(fa.soldier_id, 0.0) + h

    # Per-soldier overlap hours with each block (for accurate fairness).
    avail_hours: dict[tuple[int, int], float] = {}
    for sid in soldier_ids:
        state = states_by_id[sid]
        for block in day_blocks:
            overlap = _compute_block_overlap_minutes(state, block)
            if overlap > 0:
                avail_hours[(sid, block.block_id)] = overlap

    # Uniform fair share: total pool / number of present+eligible soldiers.
    # Include ALL soldiers who are present for ≥1 block AND eligible for ≥1
    # active task — not just those with LP variables.  This creates fairness
    # pressure from idle soldiers (their 0-hour deviation pulls the average
    # down and penalises over-assignment of active soldiers).
    assigned_sids = set(sid for sid, _, _ in possible)
    active_tids = set(tid for b in day_blocks for tid, _ in b.active_tasks)
    eligible_sids = set()
    for sid in soldier_ids:
        if not any(eligible.get(sid, {}).get(tid, False) for tid in active_tids):
            continue
        state = states_by_id[sid]
        if any(_compute_block_overlap_minutes(state, block) > 0 for block in day_blocks):
            eligible_sids.add(sid)
    eligible_sids |= assigned_sids  # ensure LP-variable soldiers are always included
    total_prior = sum(v for sid, v in prior_day_hours.items() if sid in eligible_sids)
    total_prior_segment = sum(v for sid, v in prior_segment_hours.items() if sid in eligible_sids)
    total_pool = total_day_demand + total_prior + total_prior_segment
    n_day = max(1, len(eligible_sids))
    fair_share_day = total_pool / n_day

    _log(f"  Day fairness: demand={total_day_demand:.1f}h prior={total_prior:.1f}h "
         f"prior_seg={total_prior_segment:.1f}h pool={total_pool:.1f}h "
         f"fair_share={fair_share_day:.1f}h n_avail={n_day}")
    # Log per-soldier eligibility summary.
    for sid in soldier_ids:
        n_blocks = sum(1 for b in day_blocks if any((sid, t, b.block_id) in possible for t, _ in b.active_tasks))
        prior_h = prior_day_hours.get(sid, 0.0)
        prior_seg_h = prior_segment_hours.get(sid, 0.0)
        _log(f"    soldier {sid:>2}: {n_blocks} blocks, prior={prior_h:.1f}h, prior_seg={prior_seg_h:.1f}h")

    # day_hours[s] = sum of actual overlap hours assigned.
    day_hours_var = {sid: pulp.LpVariable(f"dh_{sid}", lowBound=0) for sid in soldier_ids}
    for sid in soldier_ids:
        hour_terms = []
        for block in day_blocks:
            oh = avail_hours.get((sid, block.block_id), block.duration_minutes) / 60.0
            for tid, _ in block.active_tasks:
                if (sid, tid, block.block_id) in y:
                    hour_terms.append(oh * y[(sid, tid, block.block_id)])
        model += day_hours_var[sid] == pulp.lpSum(hour_terms), f"dh_def_{sid}"

    dev_pos = {sid: pulp.LpVariable(f"ddp_{sid}", lowBound=0) for sid in soldier_ids}
    dev_neg = {sid: pulp.LpVariable(f"ddn_{sid}", lowBound=0) for sid in soldier_ids}
    z_day = pulp.LpVariable("z_day", lowBound=0)

    for sid in soldier_ids:
        prior_h = prior_day_hours.get(sid, 0.0)
        prior_seg_h = prior_segment_hours.get(sid, 0.0)
        model += (
            day_hours_var[sid] + prior_h + prior_seg_h - fair_share_day == dev_pos[sid] - dev_neg[sid],
            f"devday_{sid}",
        )
        model += dev_pos[sid] <= z_day, f"zday_pos_{sid}"
        model += dev_neg[sid] <= z_day, f"zday_neg_{sid}"

    # ── Proximity penalty variables ──
    # Unified monotonically-decreasing curve: gap=0 (adjacent) = max cost,
    # cost decreases smoothly to zero at ~5h.  Replaces the old stretch
    # penalty (adjacent-only) + rest gap penalty (non-adjacent only).
    # Pre-compute busy expression per (soldier, block).
    busy: dict[tuple[int, int], pulp.LpAffineExpression] = {}
    for sid in soldier_ids:
        for block in day_blocks:
            terms = [
                y[(sid, tid, block.block_id)]
                for tid, _ in block.active_tasks
                if (sid, tid, block.block_id) in y
            ]
            if terms:
                busy[(sid, block.block_id)] = pulp.lpSum(terms)

    prox_pen = {}
    sorted_day = sorted(day_blocks, key=lambda b: b.start_time)
    for sid in soldier_ids:
        for i, b1 in enumerate(sorted_day):
            if (sid, b1.block_id) not in busy:
                continue
            for j in range(i + 1, len(sorted_day)):
                b2 = sorted_day[j]
                if (sid, b2.block_id) not in busy:
                    continue
                gap_h = max(0.0, (b2.start_time - b1.end_time).total_seconds() / 3600.0)
                pf = _day_gap_penalty_factor(gap_h)
                if pf < 0.01:
                    continue
                pen_var = pulp.LpVariable(
                    f"px_{sid}_{b1.block_id}_{b2.block_id}", lowBound=0, upBound=1,
                )
                prox_pen[(sid, b1.block_id, b2.block_id)] = (pen_var, pf)
                model += (
                    pen_var >= busy[(sid, b1.block_id)] + busy[(sid, b2.block_id)] - 1,
                    f"px_c_{sid}_{b1.block_id}_{b2.block_id}",
                )

    # Frozen/fixed → block rest gap penalties (only for the owning soldier).
    frozen_pen = {}
    frozen_by_soldier: dict[int, list[FrozenAssignment]] = {}
    for fa in frozen_assignments:
        frozen_by_soldier.setdefault(fa.soldier_id, []).append(fa)

    for sid in soldier_ids:
        for fi, fa in enumerate(frozen_by_soldier.get(sid, [])):
            for block in day_blocks:
                if (sid, block.block_id) not in busy:
                    continue
                gap_after = (block.start_time - fa.end_time).total_seconds() / 60.0
                gap_before = (fa.start_time - block.end_time).total_seconds() / 60.0
                gap_minutes = min(
                    gap_after if gap_after >= 0 else float("inf"),
                    gap_before if gap_before >= 0 else float("inf"),
                )
                if gap_minutes < 0:
                    continue
                gap_h = gap_minutes / 60.0
                pf = _day_gap_penalty_factor(gap_h)
                if pf < 0.01:
                    continue
                pen_var = pulp.LpVariable(
                    f"rpf_{sid}_{fi}_{block.block_id}", lowBound=0, upBound=1,
                )
                frozen_pen[(sid, fi, block.block_id)] = (pen_var, pf)
                model += (
                    pen_var >= busy[(sid, block.block_id)],
                    f"rpf_c_{sid}_{fi}_{block.block_id}",
                )

    # Prior-period → day block rest gap penalty (stepped, cross-domain uses day curve).
    # Penalize soldiers whose prior segment assignment ends close to a day block.
    prior_end_by_soldier: dict[int, datetime] = {}
    for pa in prior_assignments:
        prev = prior_end_by_soldier.get(pa.soldier_id)
        if prev is None or pa.end_time > prev:
            prior_end_by_soldier[pa.soldier_id] = pa.end_time

    prior_period_pen: dict[tuple[int, int], tuple] = {}
    for sid in soldier_ids:
        prior_end = prior_end_by_soldier.get(sid)
        if prior_end is None:
            continue
        for block in day_blocks:
            if (sid, block.block_id) not in busy:
                continue
            gap_h = (block.start_time - prior_end).total_seconds() / 3600.0
            if gap_h < 0:
                continue
            pf = _day_gap_penalty_factor(gap_h)
            if pf < 0.01:
                continue
            pen_var = pulp.LpVariable(
                f"drpp_{sid}_{block.block_id}", lowBound=0, upBound=1,
            )
            prior_period_pen[(sid, block.block_id)] = (pen_var, pf)
            model += (
                pen_var >= busy[(sid, block.block_id)],
                f"drpp_c_{sid}_{block.block_id}",
            )

    # ── Objective ──
    obj = []
    # Track named components for diagnostic breakdown.
    _obj_components: dict[str, pulp.LpAffineExpression] = {}

    # Fairness — average deviation.
    _c_fair_avg = weights.w_day_fairness * (weights.alpha / n_day) * pulp.lpSum(
        dev_pos[sid] + dev_neg[sid] for sid in soldier_ids
    )
    obj.append(_c_fair_avg)
    _obj_components["fairness_avg"] = _c_fair_avg

    # Fairness — minimax.
    _c_fair_mm = weights.w_day_minimax * weights.beta * z_day
    obj.append(_c_fair_mm)
    _obj_components["fairness_minimax"] = _c_fair_mm

    # Day excess cost: overloaded soldiers are expensive.
    excess_terms = []
    for block in day_blocks:
        for tid, _ in block.active_tasks:
            for sid in soldier_ids:
                if (sid, tid, block.block_id) in y:
                    oh = avail_hours.get((sid, block.block_id), block.duration_minutes) / 60.0
                    excess_terms.append(
                        day_excess.get(sid, 0.0) * oh * y[(sid, tid, block.block_id)]
                    )
    _c_excess = weights.w_day_points * pulp.lpSum(excess_terms) if excess_terms else 0
    obj.append(_c_excess)
    _obj_components["excess_cost"] = _c_excess

    # Proximity penalty (within-segment, unified curve).
    _prox_terms = []
    for (sid, bid1, bid2), (pen_var, penalty_factor) in prox_pen.items():
        _prox_terms.append(weights.w_day_proximity * penalty_factor * pen_var)
    _c_prox = pulp.lpSum(_prox_terms) if _prox_terms else 0
    obj.append(_c_prox)
    _obj_components["proximity"] = _c_prox

    # Frozen/fixed → block gap penalty.
    _rest_frozen_terms = []
    for (sid, fi, bid), (pen_var, penalty_factor) in frozen_pen.items():
        _rest_frozen_terms.append(weights.w_rest_frozen * penalty_factor * pen_var)
    _c_rest_frozen = pulp.lpSum(_rest_frozen_terms) if _rest_frozen_terms else 0
    obj.append(_c_rest_frozen)
    _obj_components["rest_frozen"] = _c_rest_frozen

    # Prior-period → day rest gap penalty (stepped, cross-domain day curve).
    _prior_terms = []
    for (sid, bid), (pen_var, penalty_factor) in prior_period_pen.items():
        _prior_terms.append(weights.w_rest_night_day * penalty_factor * pen_var)
    _c_prior = pulp.lpSum(_prior_terms) if _prior_terms else 0
    obj.append(_c_prior)
    _obj_components["rest_cross_domain"] = _c_prior

    # Coverage slack penalty.
    _c_cov = weights.w_coverage * pulp.lpSum(slack.values())
    obj.append(_c_cov)
    _obj_components["coverage_slack"] = _c_cov

    model += pulp.lpSum(obj), "day_objective"

    # ── Solve ──
    solver = pulp.PULP_CBC_CMD(
        msg=0,
        timeLimit=weights.time_limit_seconds,
        gapRel=weights.ratio_gap,
    )
    result_status = model.solve(solver)
    status_str = pulp.LpStatus[result_status]
    obj_val = pulp.value(model.objective) or 0.0

    if result_status not in (pulp.constants.LpStatusOptimal, 1):
        # Return empty with all uncovered.
        cov = {}
        for block in day_blocks:
            for tid, _ in block.active_tasks:
                cov[tid] = "UNCOVERED"
        return [], cov, float("inf"), status_str, {}

    # ── Extract assignments ──
    assignments = []
    coverage_status: dict[int, str] = {}
    soldier_hours: dict[int, float] = {}

    for block in day_blocks:
        for tid, conc in block.active_tasks:
            fc = frozen_cov.get((tid, block.block_id), 0)
            remaining = max(conc - fc, 0)
            assigned_count = 0
            for sid in soldier_ids:
                if (sid, tid, block.block_id) in y:
                    val = pulp.value(y[(sid, tid, block.block_id)])
                    if val is not None and val > 0.5:
                        assigned_count += 1
                        spec = spec_by_id[tid]
                        a_start = max(block.start_time, spec.start_time)
                        a_end = min(block.end_time, spec.end_time)
                        assignments.append(PlannedAssignment(
                            soldier_id=sid,
                            task_id=tid,
                            start_time=a_start,
                            end_time=a_end,
                        ))
                        h = (a_end - a_start).total_seconds() / 3600.0
                        soldier_hours[sid] = soldier_hours.get(sid, 0.0) + h
            slack_val = pulp.value(slack.get((tid, block.block_id))) or 0
            if assigned_count + fc < conc or slack_val > 0.01:
                coverage_status[tid] = "UNCOVERED"
            elif tid not in coverage_status:
                coverage_status[tid] = "OK"

    return assignments, coverage_status, obj_val, status_str, soldier_hours


# ──────────────────────────────────────────────────────────────────
# Stage 2b: Night LP Solve
# ──────────────────────────────────────────────────────────────────

def _solve_night_lp(
    night_blocks: list[TimeBlock],
    soldier_states: list[SoldierState],
    task_specs: list[TaskSpec],
    frozen_assignments: list[FrozenAssignment],
    night_excess: dict[int, float],
    prior_assignments: list[PlannedAssignment] | None = None,
    weights: LPWeights = None,
    night_start_hour: int = 23,
    night_end_hour: int = 7,
    prior_segment_weighted_hours: dict[int, float] | None = None,
    command_chain: list[int] | None = None,
    soldier_presence: dict[int, list[tuple[datetime, datetime]]] | None = None,
) -> tuple[list[PlannedAssignment], dict[int, str], float, str, dict[int, float]]:
    """Solve the night LP.

    Args:
        prior_assignments: Assignments from previously-solved segments (any domain).
            Used for cross-segment rest gap penalties.
        prior_segment_weighted_hours: Accumulated weighted hours per soldier from
            previously-solved night segments within this lp_solve() call.

    Returns (assignments, coverage_status, objective_value, status, soldier_weighted_hours).
    soldier_weighted_hours maps soldier_id to weighted hours (h × hardness × tier).
    """
    if prior_assignments is None:
        prior_assignments = []
    if prior_segment_weighted_hours is None:
        prior_segment_weighted_hours = {}
    if weights is None:
        weights = LPWeights()

    if not night_blocks:
        return [], {}, 0.0, "optimal", {}

    spec_by_id = {s.id: s for s in task_specs}
    eligible = _build_eligible_matrix(soldier_states, task_specs)
    states_by_id = {s.id: s for s in soldier_states}
    soldier_ids = [s.id for s in soldier_states if s.is_active]

    # Build prior assignment end times per soldier (from all prior segments).
    prior_end_by_soldier: dict[int, datetime] = {}
    for pa in prior_assignments:
        prev = prior_end_by_soldier.get(pa.soldier_id)
        if prev is None or pa.end_time > prev:
            prior_end_by_soldier[pa.soldier_id] = pa.end_time

    # Compute frozen coverage.
    frozen_cov: dict[tuple[int, int], int] = {}
    for fa in frozen_assignments:
        for block in night_blocks:
            for tid, _ in block.active_tasks:
                if fa.task_id == tid and fa.end_time > block.start_time and fa.start_time < block.end_time:
                    key = (tid, block.block_id)
                    frozen_cov[key] = frozen_cov.get(key, 0) + 1

    # Determine possible (soldier, task, block) triples.
    _cmd_chain = command_chain or []
    _sol_pres = soldier_presence or {}
    possible: set[tuple[int, int, int]] = set()
    for sid in soldier_ids:
        state = states_by_id[sid]
        for block in night_blocks:
            if not _soldier_available_for_block(state, block, frozen_assignments):
                continue
            for tid, _ in block.active_tasks:
                if not eligible.get(sid, {}).get(tid, False):
                    continue
                # Commander exclusion: per-block check at block midpoint.
                spec = spec_by_id.get(tid)
                if spec and not spec.include_commander and _cmd_chain:
                    mid = block.start_time + (block.end_time - block.start_time) / 2
                    cmdr = resolve_active_commander(_cmd_chain, _sol_pres, mid)
                    if cmdr == sid:
                        continue
                possible.add((sid, tid, block.block_id))

    if not possible:
        cov = {}
        for block in night_blocks:
            for tid, conc in block.active_tasks:
                fc = frozen_cov.get((tid, block.block_id), 0)
                if fc < conc:
                    cov[tid] = "UNCOVERED"
                elif tid not in cov:
                    cov[tid] = "OK"
        return [], cov, 0.0, "optimal", {}

    # Build model.
    model = pulp.LpProblem("NightLP", pulp.LpMinimize)

    y = {}
    for (sid, tid, bid) in possible:
        y[(sid, tid, bid)] = pulp.LpVariable(f"yn_{sid}_{tid}_{bid}", cat="Binary")

    slack = {}
    for block in night_blocks:
        for tid, conc in block.active_tasks:
            slack[(tid, block.block_id)] = pulp.LpVariable(
                f"sn_{tid}_{block.block_id}", lowBound=0,
            )

    # ── Constraints ──

    # Coverage.
    for block in night_blocks:
        for tid, conc in block.active_tasks:
            assigned = pulp.lpSum(
                y[(sid, tid, block.block_id)]
                for sid in soldier_ids
                if (sid, tid, block.block_id) in y
            )
            fc = frozen_cov.get((tid, block.block_id), 0)
            remaining = max(conc - fc, 0)
            model += (
                assigned + slack[(tid, block.block_id)] >= remaining,
                f"ncov_{tid}_{block.block_id}",
            )
            model += (
                assigned <= remaining,
                f"ncovub_{tid}_{block.block_id}",
            )

    # No overlap within night.
    for sid in soldier_ids:
        for block in night_blocks:
            tasks_in_block = [
                y[(sid, tid, block.block_id)]
                for tid, _ in block.active_tasks
                if (sid, tid, block.block_id) in y
            ]
            if len(tasks_in_block) > 1:
                model += (
                    pulp.lpSum(tasks_in_block) <= 1,
                    f"nnooverlap_{sid}_{block.block_id}",
                )

    # No consecutive same-task blocks: prevents chaining adjacent blocks
    # into double-length shifts (e.g. 23:00-01:30 + 01:30-04:00 = 5h).
    sorted_night_blocks = sorted(night_blocks, key=lambda b: b.start_time)
    for sid in soldier_ids:
        for i in range(len(sorted_night_blocks) - 1):
            b1 = sorted_night_blocks[i]
            b2 = sorted_night_blocks[i + 1]
            # Only adjacent blocks (gap = 0).
            if abs((b2.start_time - b1.end_time).total_seconds()) > 1:
                continue
            # For each task active in both blocks, forbid same soldier.
            for tid, _ in b1.active_tasks:
                if (sid, tid, b1.block_id) in y and (sid, tid, b2.block_id) in y:
                    model += (
                        y[(sid, tid, b1.block_id)] + y[(sid, tid, b2.block_id)] <= 1,
                        f"nnoconsec_{sid}_{tid}_{b1.block_id}_{b2.block_id}",
                    )

    # Wakeup variables and constraint.
    block_index = {b.block_id: i for i, b in enumerate(sorted_night_blocks)}

    wake = {}
    for sid in soldier_ids:
        for i, block in enumerate(sorted_night_blocks):
            bid = block.block_id
            has_tasks = any((sid, tid, bid) in y for tid, _ in block.active_tasks)
            if not has_tasks:
                continue

            wake_var = pulp.LpVariable(f"wk_{sid}_{bid}", lowBound=0, upBound=1)
            wake[(sid, bid)] = wake_var

            sum_this = pulp.lpSum(
                y[(sid, tid, bid)]
                for tid, _ in block.active_tasks
                if (sid, tid, bid) in y
            )

            if i == 0:
                model += wake_var >= sum_this, f"wk_first_{sid}_{bid}"
            else:
                prev_block = sorted_night_blocks[i - 1]
                prev_bid = prev_block.block_id
                sum_prev = pulp.lpSum(
                    y[(sid, tid, prev_bid)]
                    for tid, _ in prev_block.active_tasks
                    if (sid, tid, prev_bid) in y
                )
                if sum_prev is not None:
                    model += wake_var >= sum_this - sum_prev, f"wk_ge_{sid}_{bid}"
                else:
                    model += wake_var >= sum_this, f"wk_first2_{sid}_{bid}"

        # Wakeup cost is a soft penalty in the objective — no hard cap.
        # The w_night_wakeups weight controls how aggressively the LP
        # minimizes wakeups vs. other objectives (fairness, rest gaps).

    # ── Fairness variables ──
    # Remaining LP demand for night.
    total_night_demand = sum(
        max(conc - frozen_cov.get((tid, block.block_id), 0), 0)
        * block.duration_minutes / 60.0
        for block in night_blocks
        for tid, conc in block.active_tasks
    )

    # Prior (frozen/fixed) weighted hours per soldier within the night period.
    prior_night_weighted: dict[int, float] = {}
    for fa in frozen_assignments:
        for block in night_blocks:
            ov_start = max(fa.start_time, block.start_time)
            ov_end = min(fa.end_time, block.end_time)
            if ov_end > ov_start:
                h = (ov_end - ov_start).total_seconds() / 3600.0
                spec = spec_by_id.get(fa.task_id)
                hardness = spec.hardness if spec else 3
                tier = block.night_quality_tier or 1
                wh = h * hardness * tier
                prior_night_weighted[fa.soldier_id] = prior_night_weighted.get(fa.soldier_id, 0.0) + wh

    # Per-soldier overlap hours with each night block.
    night_avail_hours: dict[tuple[int, int], float] = {}
    for sid in soldier_ids:
        state = states_by_id[sid]
        for block in night_blocks:
            overlap = _compute_block_overlap_minutes(state, block)
            if overlap > 0:
                night_avail_hours[(sid, block.block_id)] = overlap

    # Include ALL soldiers present for ≥1 night block AND eligible for ≥1 task.
    assigned_night_sids = set(sid for sid, _, _ in possible)
    active_night_tids = set(tid for b in night_blocks for tid, _ in b.active_tasks)
    eligible_night_sids = set()
    for sid in soldier_ids:
        if not any(eligible.get(sid, {}).get(tid, False) for tid in active_night_tids):
            continue
        state = states_by_id[sid]
        if any(_compute_block_overlap_minutes(state, block) > 0 for block in night_blocks):
            eligible_night_sids.add(sid)
    eligible_night_sids |= assigned_night_sids
    n_night = max(1, len(eligible_night_sids))

    night_hours_var = {sid: pulp.LpVariable(f"nh_{sid}", lowBound=0) for sid in soldier_ids}
    for sid in soldier_ids:
        hour_terms = []
        for block in night_blocks:
            oh = night_avail_hours.get((sid, block.block_id), block.duration_minutes) / 60.0
            for tid, _ in block.active_tasks:
                if (sid, tid, block.block_id) in y:
                    spec = spec_by_id.get(tid)
                    hardness = spec.hardness if spec else 3
                    tier = block.night_quality_tier or 1
                    hour_terms.append(oh * hardness * tier * y[(sid, tid, block.block_id)])
        model += night_hours_var[sid] == pulp.lpSum(hour_terms), f"nh_def_{sid}"

    dev_pos = {sid: pulp.LpVariable(f"dnp_{sid}", lowBound=0) for sid in soldier_ids}
    dev_neg = {sid: pulp.LpVariable(f"dnn_{sid}", lowBound=0) for sid in soldier_ids}
    z_night = pulp.LpVariable("z_night", lowBound=0)

    # Fair share for weighted night hours — proportional to presence.
    total_night_weighted_demand = sum(
        max(conc - frozen_cov.get((tid, block.block_id), 0), 0)
        * block.duration_minutes / 60.0
        * (spec_by_id[tid].hardness if tid in spec_by_id else 3)
        * (block.night_quality_tier or 1)
        for block in night_blocks
        for tid, conc in block.active_tasks
    )
    total_prior_night_w = sum(v for sid, v in prior_night_weighted.items() if sid in eligible_night_sids)
    total_prior_seg_night_w = sum(v for sid, v in prior_segment_weighted_hours.items() if sid in eligible_night_sids)
    total_night_pool = total_night_weighted_demand + total_prior_night_w + total_prior_seg_night_w
    fair_share_night_weighted = total_night_pool / n_night

    _log(f"  Night fairness: demand={total_night_demand:.1f}h "
         f"weighted_pool={total_night_pool:.1f} "
         f"prior_seg_w={total_prior_seg_night_w:.1f} "
         f"fair_share={fair_share_night_weighted:.1f} n_avail={n_night}")

    for sid in soldier_ids:
        prior_wh = prior_night_weighted.get(sid, 0.0)
        prior_seg_wh = prior_segment_weighted_hours.get(sid, 0.0)
        model += (
            night_hours_var[sid] + prior_wh + prior_seg_wh - fair_share_night_weighted == dev_pos[sid] - dev_neg[sid],
            f"devnight_{sid}",
        )
        model += dev_pos[sid] <= z_night, f"znight_pos_{sid}"
        model += dev_neg[sid] <= z_night, f"znight_neg_{sid}"

    # Normalized excess for quality steering.
    excess_vals = [night_excess.get(sid, 0.0) for sid in soldier_ids]
    max_exc = max(excess_vals) if excess_vals else 0.0
    min_exc = min(excess_vals) if excess_vals else 0.0
    normalized_excess: dict[int, float] = {}
    for sid in soldier_ids:
        if max_exc == min_exc:
            normalized_excess[sid] = 0.5
        else:
            normalized_excess[sid] = (night_excess.get(sid, 0.0) - min_exc) / (max_exc - min_exc)

    # ── Proximity penalty (within-segment, unified curve) ──
    # Monotonically decreasing: gap=0 (adjacent) = max cost, cost→0 at ~6h.
    # Replaces old stretch penalty + rest gap penalty.

    # Pre-compute busy expression per (soldier, block).
    busy: dict[tuple[int, int], pulp.LpAffineExpression] = {}
    for sid in soldier_ids:
        for block in night_blocks:
            terms = [
                y[(sid, tid, block.block_id)]
                for tid, _ in block.active_tasks
                if (sid, tid, block.block_id) in y
            ]
            if terms:
                busy[(sid, block.block_id)] = pulp.lpSum(terms)

    prox_pen = {}
    for sid in soldier_ids:
        for i, b1 in enumerate(sorted_night_blocks):
            if (sid, b1.block_id) not in busy:
                continue
            for j in range(i + 1, len(sorted_night_blocks)):
                b2 = sorted_night_blocks[j]
                if (sid, b2.block_id) not in busy:
                    continue
                gap_h = max(0.0, (b2.start_time - b1.end_time).total_seconds() / 3600.0)
                pf = _night_gap_penalty_factor(gap_h)
                if pf < 0.01:
                    continue
                pen_var = pulp.LpVariable(
                    f"npx_{sid}_{b1.block_id}_{b2.block_id}", lowBound=0, upBound=1,
                )
                prox_pen[(sid, b1.block_id, b2.block_id)] = (pen_var, pf)
                model += (
                    pen_var >= busy[(sid, b1.block_id)] + busy[(sid, b2.block_id)] - 1,
                    f"npx_c_{sid}_{b1.block_id}_{b2.block_id}",
                )

    # Frozen/fixed → night block gap penalties (only for the owning soldier).
    frozen_pen = {}
    frozen_by_soldier: dict[int, list] = {}
    for fa in frozen_assignments:
        frozen_by_soldier.setdefault(fa.soldier_id, []).append(fa)

    for sid in soldier_ids:
        for fi, fa in enumerate(frozen_by_soldier.get(sid, [])):
            for block in night_blocks:
                if (sid, block.block_id) not in busy:
                    continue
                gap_after = (block.start_time - fa.end_time).total_seconds() / 3600.0
                gap_before = (fa.start_time - block.end_time).total_seconds() / 3600.0
                gap_h = min(
                    gap_after if gap_after >= 0 else float("inf"),
                    gap_before if gap_before >= 0 else float("inf"),
                )
                pf = _night_gap_penalty_factor(gap_h)
                if pf < 0.01:
                    continue
                pen_var = pulp.LpVariable(
                    f"nrpf_{sid}_{fi}_{block.block_id}", lowBound=0, upBound=1,
                )
                frozen_pen[(sid, fi, block.block_id)] = (pen_var, pf)
                model += (
                    pen_var >= busy[(sid, block.block_id)],
                    f"nrpf_c_{sid}_{fi}_{block.block_id}",
                )

    # Day→night rest gap penalty: cross-domain uses the day stepped curve
    # (soldier is still awake, less harmful than mid-sleep disruption).
    # Soft cost — coverage never sacrificed for rest.
    day_night_pen = {}
    for sid in soldier_ids:
        day_end = prior_end_by_soldier.get(sid)
        if day_end is None:
            continue
        for block in sorted_night_blocks:
            if (sid, block.block_id) not in busy:
                continue
            gap_h = (block.start_time - day_end).total_seconds() / 3600.0
            if gap_h < 0:
                continue
            pf = _day_gap_penalty_factor(gap_h)
            if pf < 0.01:
                continue
            pen_var = pulp.LpVariable(
                f"nrpd_{sid}_{block.block_id}", lowBound=0, upBound=1,
            )
            day_night_pen[(sid, block.block_id)] = (pen_var, pf)
            model += (
                pen_var >= busy[(sid, block.block_id)],
                f"nrpd_c_{sid}_{block.block_id}",
            )

    # ── Objective ──
    obj = []
    _obj_components: dict[str, pulp.LpAffineExpression] = {}

    # Wakeup minimization (PRIMARY).
    _c_wakeup = weights.w_night_wakeups * pulp.lpSum(wake.values()) if wake else 0
    obj.append(_c_wakeup)
    _obj_components["wakeup_cost"] = _c_wakeup

    # Night quality steering.
    quality_terms = []
    for block in night_blocks:
        tier_cost = block.night_quality_tier or 1
        for tid, _ in block.active_tasks:
            for sid in soldier_ids:
                if (sid, tid, block.block_id) in y:
                    oh = night_avail_hours.get((sid, block.block_id), block.duration_minutes) / 60.0
                    cost = normalized_excess.get(sid, 0.5) * tier_cost * oh
                    quality_terms.append(cost * y[(sid, tid, block.block_id)])
    _c_quality = weights.w_night_hardship * pulp.lpSum(quality_terms) if quality_terms else 0
    obj.append(_c_quality)
    _obj_components["quality_steering"] = _c_quality

    # Fairness.
    _c_fair_avg = weights.w_night_fairness * (weights.alpha / n_night) * pulp.lpSum(
        dev_pos[sid] + dev_neg[sid] for sid in soldier_ids
    )
    obj.append(_c_fair_avg)
    _obj_components["fairness_avg"] = _c_fair_avg

    _c_fair_mm = weights.w_night_minimax * weights.beta * z_night
    obj.append(_c_fair_mm)
    _obj_components["fairness_minimax"] = _c_fair_mm

    # Night excess cost.
    excess_terms = []
    for block in night_blocks:
        for tid, _ in block.active_tasks:
            for sid in soldier_ids:
                if (sid, tid, block.block_id) in y:
                    oh = night_avail_hours.get((sid, block.block_id), block.duration_minutes) / 60.0
                    excess_terms.append(
                        night_excess.get(sid, 0.0) * oh * y[(sid, tid, block.block_id)]
                    )
    _c_excess = weights.w_night_points * pulp.lpSum(excess_terms) if excess_terms else 0
    obj.append(_c_excess)
    _obj_components["excess_cost"] = _c_excess

    # Proximity penalty (within-segment, unified curve).
    _prox_terms = []
    for (sid, bid1, bid2), (pen_var, penalty_factor) in prox_pen.items():
        _prox_terms.append(weights.w_night_proximity * penalty_factor * pen_var)
    _c_prox = pulp.lpSum(_prox_terms) if _prox_terms else 0
    obj.append(_c_prox)
    _obj_components["proximity"] = _c_prox

    # Frozen/fixed → block gap penalty.
    _rest_frozen_terms = []
    for (sid, fi, bid), (pen_var, penalty_factor) in frozen_pen.items():
        _rest_frozen_terms.append(weights.w_rest_frozen * penalty_factor * pen_var)
    _c_rest_frozen = pulp.lpSum(_rest_frozen_terms) if _rest_frozen_terms else 0
    obj.append(_c_rest_frozen)
    _obj_components["rest_frozen"] = _c_rest_frozen

    # Day→night rest gap penalty (cross-domain, uses _day_gap_penalty_factor).
    _cross_terms = []
    for (sid, bid), (pen_var, penalty_factor) in day_night_pen.items():
        _cross_terms.append(weights.w_rest_day_night * penalty_factor * pen_var)
    _c_cross = pulp.lpSum(_cross_terms) if _cross_terms else 0
    obj.append(_c_cross)
    _obj_components["rest_cross_domain"] = _c_cross

    # Coverage slack penalty.
    _c_cov = weights.w_coverage * pulp.lpSum(slack.values())
    obj.append(_c_cov)
    _obj_components["coverage_slack"] = _c_cov

    model += pulp.lpSum(obj), "night_objective"

    # ── Solve ──
    solver = pulp.PULP_CBC_CMD(
        msg=0,
        timeLimit=weights.time_limit_seconds,
        gapRel=weights.ratio_gap,
    )
    result_status = model.solve(solver)
    status_str = pulp.LpStatus[result_status]
    obj_val = pulp.value(model.objective) or 0.0

    if result_status not in (pulp.constants.LpStatusOptimal, 1):
        cov = {}
        for block in night_blocks:
            for tid, _ in block.active_tasks:
                cov[tid] = "UNCOVERED"
        return [], cov, float("inf"), status_str, {}

    # ── Extract assignments ──
    assignments = []
    coverage_status: dict[int, str] = {}
    soldier_weighted_hours: dict[int, float] = {}

    for block in night_blocks:
        for tid, conc in block.active_tasks:
            fc = frozen_cov.get((tid, block.block_id), 0)
            assigned_count = 0
            for sid in soldier_ids:
                if (sid, tid, block.block_id) in y:
                    val = pulp.value(y[(sid, tid, block.block_id)])
                    if val is not None and val > 0.5:
                        assigned_count += 1
                        spec = spec_by_id[tid]
                        a_start = max(block.start_time, spec.start_time)
                        a_end = min(block.end_time, spec.end_time)
                        assignments.append(PlannedAssignment(
                            soldier_id=sid,
                            task_id=tid,
                            start_time=a_start,
                            end_time=a_end,
                        ))
                        h = (a_end - a_start).total_seconds() / 3600.0
                        hardness = spec.hardness if spec else 3
                        tier = block.night_quality_tier or 1
                        wh = h * hardness * tier
                        soldier_weighted_hours[sid] = soldier_weighted_hours.get(sid, 0.0) + wh
            slack_val = pulp.value(slack.get((tid, block.block_id))) or 0
            if assigned_count + fc < conc or slack_val > 0.01:
                coverage_status[tid] = "UNCOVERED"
            elif tid not in coverage_status:
                coverage_status[tid] = "OK"

    return assignments, coverage_status, obj_val, status_str, soldier_weighted_hours


# ──────────────────────────────────────────────────────────────────
# Fixed task handling
# ──────────────────────────────────────────────────────────────────

def _task_calendar_day(
    spec: TaskSpec,
    night_start_hour: int,
    night_end_hour: int,
) -> object:
    """Determine the calendar day for a fixed task using its midpoint.

    For night-period tasks, use night_date bucketing: hours >= night_start_hour
    belong to that calendar day, hours < night_end_hour belong to the previous day.
    """
    midpoint = spec.start_time + (spec.end_time - spec.start_time) / 2
    mid_h = midpoint.hour + midpoint.minute / 60.0
    if night_end_hour <= night_start_hour:
        is_night = mid_h >= night_start_hour or mid_h < night_end_hour
    else:
        is_night = night_start_hour <= mid_h < night_end_hour
    if is_night:
        if mid_h >= night_start_hour:
            return midpoint.date()
        return (midpoint - timedelta(days=1)).date()
    return midpoint.date()


def _task_is_night_period(
    spec: TaskSpec,
    night_start_hour: int,
    night_end_hour: int,
) -> bool:
    """Check if a fixed task's midpoint falls in the night window."""
    midpoint = spec.start_time + (spec.end_time - spec.start_time) / 2
    mid_h = midpoint.hour + midpoint.minute / 60.0
    if night_end_hour <= night_start_hour:
        return mid_h >= night_start_hour or mid_h < night_end_hour
    return night_start_hour <= mid_h < night_end_hour


def _solve_fixed_tasks(
    task_specs: list[TaskSpec],
    soldier_states: list[SoldierState],
    frozen_assignments: list[FrozenAssignment],
    fp_snapped: datetime | None = None,
    night_start_hour: int = 23,
    night_end_hour: int = 7,
    day_excess: dict[int, float] | None = None,
    night_excess: dict[int, float] | None = None,
    weights: LPWeights | None = None,
    command_chain: list[int] | None = None,
    soldier_presence: dict[int, list[tuple[datetime, datetime]]] | None = None,
) -> tuple[list[PlannedAssignment], dict[int, str]]:
    """Handle fixed (non-fractionable) tasks with a simple LP.

    Fixed tasks are assigned as whole-window blocks, not split into time blocks.
    Includes same-day stacking penalty and excess-aware costs.
    """
    if day_excess is None:
        day_excess = {}
    if night_excess is None:
        night_excess = {}
    if weights is None:
        weights = LPWeights()

    fixed_specs = [s for s in task_specs if not s.is_fractionable]
    if not fixed_specs:
        return [], {}

    eligible = _build_eligible_matrix(soldier_states, fixed_specs)
    soldier_ids = [s.id for s in soldier_states if s.is_active]
    states_by_id = {s.id: s for s in soldier_states}

    # Frozen coverage for fixed tasks.
    frozen_cov_fixed: dict[int, int] = {}
    for fa in frozen_assignments:
        for spec in fixed_specs:
            if fa.task_id == spec.id:
                frozen_cov_fixed[spec.id] = frozen_cov_fixed.get(spec.id, 0) + 1

    model = pulp.LpProblem("FixedTasks", pulp.LpMinimize)

    # y[s,t] binary — soldier s assigned to fixed task t for its full window.
    _cmd_chain = command_chain or []
    _sol_pres = soldier_presence or {}
    y = {}
    for spec in fixed_specs:
        for sid in soldier_ids:
            if not eligible.get(sid, {}).get(spec.id, False):
                continue
            state = states_by_id[sid]
            # Must be available for full task window.
            if not _soldier_available_for_window(state, spec.start_time, spec.end_time, frozen_assignments):
                continue
            # Commander exclusion: resolve at task midpoint for fixed tasks.
            if not spec.include_commander and _cmd_chain:
                mid = spec.start_time + (spec.end_time - spec.start_time) / 2
                cmdr = resolve_active_commander(_cmd_chain, _sol_pres, mid)
                if cmdr == sid:
                    continue
            y[(sid, spec.id)] = pulp.LpVariable(f"yf_{sid}_{spec.id}", cat="Binary")

    slack = {}
    for spec in fixed_specs:
        slack[spec.id] = pulp.LpVariable(f"sf_{spec.id}", lowBound=0)

    # Coverage.
    for spec in fixed_specs:
        assigned = pulp.lpSum(
            y[(sid, spec.id)] for sid in soldier_ids if (sid, spec.id) in y
        )
        fc = frozen_cov_fixed.get(spec.id, 0)
        remaining = max(spec.concurrent_required - fc, 0)
        model += assigned + slack[spec.id] >= remaining, f"fcov_{spec.id}"
        model += assigned <= remaining, f"fcovub_{spec.id}"

    # No overlap: a soldier can only be on one fixed task at a time.
    # Check for overlapping fixed tasks.
    for sid in soldier_ids:
        for i, s1 in enumerate(fixed_specs):
            for s2 in fixed_specs[i + 1:]:
                if s1.end_time > s2.start_time and s1.start_time < s2.end_time:
                    if (sid, s1.id) in y and (sid, s2.id) in y:
                        model += (
                            y[(sid, s1.id)] + y[(sid, s2.id)] <= 1,
                            f"fnooverlap_{sid}_{s1.id}_{s2.id}",
                        )

    obj_terms = [weights.w_coverage * pulp.lpSum(slack.values())]

    # ── Same-day stacking penalty ──
    # Group fixed tasks by calendar day.
    task_day = {spec.id: _task_calendar_day(spec, night_start_hour, night_end_hour)
                for spec in fixed_specs}
    spec_by_id = {spec.id: spec for spec in fixed_specs}

    stack_vars = {}
    for i, s1 in enumerate(fixed_specs):
        for s2 in fixed_specs[i + 1:]:
            # Same calendar day?
            if task_day[s1.id] != task_day[s2.id]:
                continue
            # Non-overlapping? (overlapping pairs already have hard no-overlap constraint)
            if s1.end_time > s2.start_time and s1.start_time < s2.end_time:
                continue
            # Compute gap in hours (assuming t1 ends first).
            if s1.end_time <= s2.start_time:
                gap_h = (s2.start_time - s1.end_time).total_seconds() / 3600.0
            else:
                gap_h = (s1.start_time - s2.end_time).total_seconds() / 3600.0
            pf = _day_gap_penalty_factor(gap_h)
            if pf < 0.01:
                continue
            t1_dur_h = (s1.end_time - s1.start_time).total_seconds() / 3600.0
            t2_dur_h = (s2.end_time - s2.start_time).total_seconds() / 3600.0
            combined_hours = t1_dur_h + t2_dur_h
            per_pair_cost = min(weights.w_fixed_stack * pf * combined_hours, 500.0)

            for sid in soldier_ids:
                if (sid, s1.id) not in y or (sid, s2.id) not in y:
                    continue
                sv = pulp.LpVariable(
                    f"fstack_{sid}_{s1.id}_{s2.id}", lowBound=0, upBound=1,
                )
                stack_vars[(sid, s1.id, s2.id)] = sv
                model += (
                    sv >= y[(sid, s1.id)] + y[(sid, s2.id)] - 1,
                    f"fstack_c_{sid}_{s1.id}_{s2.id}",
                )
                obj_terms.append(per_pair_cost * sv)

    # ── Excess-aware cost for fixed tasks ──
    for spec in fixed_specs:
        task_hours = (spec.end_time - spec.start_time).total_seconds() / 3600.0
        is_night = _task_is_night_period(spec, night_start_hour, night_end_hour)
        excess_dict = night_excess if is_night else day_excess
        for sid in soldier_ids:
            if (sid, spec.id) not in y:
                continue
            exc = excess_dict.get(sid, 0.0)
            if exc > 0.01:
                obj_terms.append(
                    weights.w_fixed_excess * exc * task_hours * y[(sid, spec.id)]
                )

    model += pulp.lpSum(obj_terms), "fixed_obj"

    solver = pulp.PULP_CBC_CMD(msg=0, timeLimit=5)
    result_status = model.solve(solver)

    assignments = []
    coverage_status: dict[int, str] = {}

    for spec in fixed_specs:
        fc = frozen_cov_fixed.get(spec.id, 0)
        assigned_count = 0
        for sid in soldier_ids:
            if (sid, spec.id) in y:
                val = pulp.value(y[(sid, spec.id)])
                if val is not None and val > 0.5:
                    assigned_count += 1
                    asgn_start = spec.start_time
                    if fp_snapped and asgn_start < fp_snapped:
                        asgn_start = fp_snapped
                    assignments.append(PlannedAssignment(
                        soldier_id=sid,
                        task_id=spec.id,
                        start_time=asgn_start,
                        end_time=spec.end_time,
                    ))
        slack_val = pulp.value(slack.get(spec.id)) or 0
        if assigned_count + fc < spec.concurrent_required or slack_val > 0.01:
            coverage_status[spec.id] = "UNCOVERED"
        else:
            coverage_status[spec.id] = "OK"

    return assignments, coverage_status


def _soldier_available_for_window(
    state: SoldierState,
    start: datetime,
    end: datetime,
    frozen_assignments: list[FrozenAssignment],
) -> bool:
    """Check if soldier is present and not frozen for a full time window.

    Uses merged presence intervals to handle the midnight gap.
    """
    if not state.is_active:
        return False

    merged = _merge_present_intervals(state)
    present = False
    for pi_start, pi_end in merged:
        if pi_start <= start and pi_end >= end:
            present = True
            break
    if not present:
        return False

    for fa in frozen_assignments:
        if fa.soldier_id == state.id:
            if fa.end_time > start and fa.start_time < end:
                return False
    return True


# ──────────────────────────────────────────────────────────────────
# Stage 3: Configuration selection + main entry point
# ──────────────────────────────────────────────────────────────────

def lp_solve(
    soldier_states: list[SoldierState],
    task_specs: list[TaskSpec],
    frozen_assignments: list[FrozenAssignment],
    freeze_point: datetime,
    night_start_hour: int,
    night_end_hour: int,
    weights: LPWeights,
    effective_ledger: dict,
    command_chain: list[int] | None = None,
) -> LPSolution:
    """Build and solve the two-stage block-based LP scheduling model.

    For each target block length, generates blocks, solves day LP then night LP,
    and picks the configuration with the lowest total objective.
    """
    t_start = time.time()

    if not task_specs or not soldier_states:
        return LPSolution(
            assignments=[],
            coverage_status={t.id: "UNCOVERED" for t in task_specs},
            metrics=SolutionMetrics(),
            solve_time_seconds=0.0,
            status="optimal",
        )

    # Clip task windows to freeze_point so the LP never creates assignments
    # starting before now.  Without this, a second reconcile would see
    # assignments with start_time < now, classify them as "in-progress",
    # freeze them, and stack new ones on top instead of replacing them.
    fp_snapped = _snap_up_to_grid(freeze_point)
    for spec in task_specs:
        if spec.start_time < fp_snapped:
            spec.start_time = fp_snapped

    # Drop specs whose window collapsed after clipping.
    task_specs = [s for s in task_specs if s.end_time > s.start_time]

    if not task_specs:
        return LPSolution(
            assignments=[],
            coverage_status={},
            metrics=SolutionMetrics(),
            solve_time_seconds=time.time() - t_start,
            status="optimal",
        )

    # Compute day_excess and night_excess from effective_ledger.
    day_excess = {}
    night_excess = {}
    for sid, led in effective_ledger.items():
        day_excess[sid] = led.get("day_points", 0.0)
        night_excess[sid] = led.get("night_points", 0.0)

    # Build soldier presence map for commander resolution.
    _cmd_chain = command_chain or []
    _sol_pres = _build_soldier_presence_map(soldier_states) if _cmd_chain else {}

    # Handle fixed tasks first (independent of block configuration).
    fixed_assignments, fixed_coverage = _solve_fixed_tasks(
        task_specs, soldier_states, frozen_assignments, fp_snapped,
        night_start_hour=night_start_hour,
        night_end_hour=night_end_hour,
        day_excess=day_excess,
        night_excess=night_excess,
        weights=weights,
        command_chain=_cmd_chain,
        soldier_presence=_sol_pres,
    )

    # Add fixed assignments to frozen list so block LPs account for them.
    augmented_frozen = list(frozen_assignments) + [
        FrozenAssignment(
            soldier_id=pa.soldier_id,
            task_id=pa.task_id,
            start_time=pa.start_time,
            end_time=pa.end_time,
        )
        for pa in fixed_assignments
    ]

    # Only fractionable tasks go through block generation.
    frac_specs = [s for s in task_specs if s.is_fractionable]

    best_result: Optional[ConfigResult] = None

    # Sweet-spot penalty parameters.
    _sweet_max = weights.sweet_spot_max_minutes

    # Try largest block lengths first — they produce smaller models and solve
    # fast, establishing a bound.  Then try shorter block lengths if time allows.
    time_budget = max(weights.time_limit_seconds * len(weights.target_block_lengths), 10.0)
    for target_length in sorted(weights.target_block_lengths, reverse=True):
        # Stop if we have a valid result and time budget is exhausted.
        if best_result is not None and (time.time() - t_start) >= time_budget:
            _log(f"  target={target_length}min: SKIPPED (time budget exhausted)")
            continue
        t_config = time.time()
        result = _generate_all_blocks(
            frac_specs, target_length, night_start_hour, night_end_hour, weights,
        )
        if result is None:
            _log(f"  target={target_length}min: SKIPPED (block size out of range)")
            continue

        day_blocks, night_blocks, segments = result

        # Sweet-spot penalty: configs above sweet_spot_max_minutes are penalized.
        if target_length > _sweet_max:
            _overshoot = (target_length - _sweet_max) / 30.0
            _sweet_penalty = weights.w_long_block_penalty * (_overshoot ** weights.long_block_exponent)
        else:
            _sweet_penalty = 0.0

        _log(f"  target={target_length}min: {len(day_blocks)} day blocks, "
             f"{len(night_blocks)} night blocks, {len(segments)} segments"
             f" (sweet_penalty={_sweet_penalty:.1f})")

        # Solve segments in chronological order.
        all_seg_assignments: list[PlannedAssignment] = []
        all_seg_coverage: dict[int, str] = {}
        accumulated_day_hours: dict[int, float] = {}       # sid -> raw hours from prior day segments
        accumulated_night_weighted: dict[int, float] = {}  # sid -> weighted hours from prior night segments
        total_obj = 0.0
        all_day_asgn: list[PlannedAssignment] = []
        all_night_asgn: list[PlannedAssignment] = []
        config_day_status = "optimal"
        config_night_status = "optimal"
        pruned = False

        for seg in segments:
            if seg.seg_type == 'day':
                asgn, cov, obj, status, seg_soldier_hours = _solve_day_lp(
                    seg.blocks, soldier_states, frac_specs, augmented_frozen,
                    day_excess, weights, night_start_hour, night_end_hour,
                    prior_assignments=all_seg_assignments,
                    prior_segment_hours=accumulated_day_hours,
                    command_chain=_cmd_chain,
                    soldier_presence=_sol_pres,
                )
                config_day_status = status
                all_day_asgn.extend(asgn)
                # Accumulate day hours for subsequent day segments.
                for sid, h in seg_soldier_hours.items():
                    accumulated_day_hours[sid] = accumulated_day_hours.get(sid, 0.0) + h
            else:
                asgn, cov, obj, status, seg_soldier_wh = _solve_night_lp(
                    seg.blocks, soldier_states, frac_specs, augmented_frozen,
                    night_excess,
                    prior_assignments=all_seg_assignments,
                    weights=weights,
                    night_start_hour=night_start_hour,
                    night_end_hour=night_end_hour,
                    prior_segment_weighted_hours=accumulated_night_weighted,
                    command_chain=_cmd_chain,
                    soldier_presence=_sol_pres,
                )
                config_night_status = status
                all_night_asgn.extend(asgn)
                # Accumulate night weighted hours for subsequent night segments.
                for sid, wh in seg_soldier_wh.items():
                    accumulated_night_weighted[sid] = accumulated_night_weighted.get(sid, 0.0) + wh

            all_seg_assignments.extend(asgn)
            total_obj += obj
            # Merge coverage (worst status wins).
            for tid, s in cov.items():
                if s == "UNCOVERED":
                    all_seg_coverage[tid] = "UNCOVERED"
                elif tid not in all_seg_coverage:
                    all_seg_coverage[tid] = "OK"

            # Early termination: if running total (with penalty) exceeds best.
            if best_result is not None and (total_obj + _sweet_penalty) >= best_result.total_objective:
                dt_config = time.time() - t_config
                _log(f"    running_adj={total_obj + _sweet_penalty:.2f} >= best {best_result.total_objective:.2f}, PRUNED ({dt_config:.1f}s)")
                pruned = True
                break

        dt_config = time.time() - t_config

        # Adjusted objective = raw + sweet-spot penalty.
        raw_obj = total_obj
        adjusted_obj = total_obj + _sweet_penalty

        # Check if this config is worse than best — skip it.
        if best_result is not None and adjusted_obj >= best_result.total_objective:
            _log(f"    adjusted_obj={adjusted_obj:.2f} (raw={raw_obj:.2f} + penalty={_sweet_penalty:.1f}) >= best {best_result.total_objective:.2f}, not chosen ({dt_config:.1f}s)")
            continue

        _log(f"    adjusted_obj={adjusted_obj:.2f} (raw={raw_obj:.2f} + penalty={_sweet_penalty:.1f}) ({dt_config:.1f}s)")

        config = ConfigResult(
            target_length=target_length,
            day_blocks=day_blocks,
            night_blocks=night_blocks,
            day_assignments=all_day_asgn,
            night_assignments=all_night_asgn,
            day_objective=0.0,
            night_objective=0.0,
            total_objective=adjusted_obj,
            day_coverage_status={tid: s for tid, s in all_seg_coverage.items()
                                 if any(tid in dict(b.active_tasks) for b in day_blocks)},
            night_coverage_status={tid: s for tid, s in all_seg_coverage.items()
                                   if any(tid in dict(b.active_tasks) for b in night_blocks)},
            day_status=config_day_status,
            night_status=config_night_status,
            metrics=SolutionMetrics(),
        )

        if best_result is None or adjusted_obj < best_result.total_objective:
            best_result = config

    # If no valid configuration found, return all uncovered.
    if best_result is None:
        solve_time = time.time() - t_start
        return LPSolution(
            assignments=fixed_assignments,
            coverage_status={
                **{t.id: "UNCOVERED" for t in frac_specs},
                **fixed_coverage,
            },
            metrics=SolutionMetrics(
                uncovered_task_ids=[t.id for t in frac_specs],
            ),
            solve_time_seconds=solve_time,
            status="feasible" if fixed_assignments else "infeasible",
        )

    # Merge all assignments.
    all_assignments = fixed_assignments + best_result.day_assignments + best_result.night_assignments

    # Merge coverage status.
    all_coverage = dict(fixed_coverage)
    all_coverage.update(best_result.day_coverage_status)
    all_coverage.update(best_result.night_coverage_status)
    # Ensure tasks in both day and night get worst status.
    for tid in set(best_result.day_coverage_status.keys()) | set(best_result.night_coverage_status.keys()):
        if (best_result.day_coverage_status.get(tid) == "UNCOVERED"
                or best_result.night_coverage_status.get(tid) == "UNCOVERED"):
            all_coverage[tid] = "UNCOVERED"

    # Ensure all tasks have a coverage status.
    for spec in task_specs:
        if spec.id not in all_coverage:
            # Task had no blocks (maybe entirely outside periods) — check if covered by fixed.
            all_coverage[spec.id] = "UNCOVERED"

    # Compute metrics.
    metrics = _compute_metrics(
        all_assignments, soldier_states, best_result, weights,
    )
    metrics.uncovered_task_ids = [tid for tid, s in all_coverage.items() if s == "UNCOVERED"]

    solve_time = time.time() - t_start
    _log(f"Best config: target={best_result.target_length}min, "
         f"total_obj={best_result.total_objective:.2f}, "
         f"solve_time={solve_time:.2f}s")

    # Determine overall status.
    day_ok = best_result.day_status in ("Optimal", "optimal")
    night_ok = best_result.night_status in ("Optimal", "optimal")
    if day_ok and night_ok:
        status = "optimal"
    elif best_result.day_status == "Infeasible" or best_result.night_status == "Infeasible":
        status = "infeasible"
    else:
        status = "feasible"

    # Log compact quality scorecard.
    _log_scorecard(all_assignments, all_coverage, task_specs, soldier_states)

    return LPSolution(
        assignments=all_assignments,
        coverage_status=all_coverage,
        metrics=metrics,
        solve_time_seconds=solve_time,
        status=status,
    )


def _compute_metrics(
    assignments: list[PlannedAssignment],
    soldier_states: list[SoldierState],
    config: ConfigResult,
    weights: LPWeights,
) -> SolutionMetrics:
    """Compute summary metrics from the winning configuration."""
    metrics = SolutionMetrics()

    # Per-soldier hours.
    from collections import defaultdict
    day_hours: dict[int, float] = defaultdict(float)
    night_hours: dict[int, float] = defaultdict(float)

    for pa in config.day_assignments:
        dur_h = (pa.end_time - pa.start_time).total_seconds() / 3600.0
        day_hours[pa.soldier_id] += dur_h

    for pa in config.night_assignments:
        dur_h = (pa.end_time - pa.start_time).total_seconds() / 3600.0
        night_hours[pa.soldier_id] += dur_h

    # Day fairness.
    if day_hours:
        avg = sum(day_hours.values()) / max(len(day_hours), 1)
        devs = [abs(h - avg) for h in day_hours.values()]
        metrics.avg_day_deviation = sum(devs) / len(devs) if devs else 0
        metrics.max_day_deviation = max(devs) if devs else 0

    # Night fairness.
    if night_hours:
        avg = sum(night_hours.values()) / max(len(night_hours), 1)
        devs = [abs(h - avg) for h in night_hours.values()]
        metrics.avg_night_deviation = sum(devs) / len(devs) if devs else 0
        metrics.max_night_deviation = max(devs) if devs else 0

    # Count night starts (wakeups).
    metrics.total_night_restarts = len(config.night_assignments)  # Each assignment is one block = one potential wakeup.

    # Count day starts.
    metrics.total_day_starts = len(config.day_assignments)

    # Shortest gap.
    soldier_blocks: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
    for pa in assignments:
        soldier_blocks[pa.soldier_id].append((pa.start_time, pa.end_time))

    min_gap = float("inf")
    for sid, blocks in soldier_blocks.items():
        blocks.sort()
        for i in range(1, len(blocks)):
            gap = (blocks[i][0] - blocks[i - 1][1]).total_seconds() / 60.0
            if gap > 0:
                min_gap = min(min_gap, gap)

    metrics.shortest_gap_minutes = min_gap if min_gap != float("inf") else 0.0

    return metrics


def _log_scorecard(
    assignments: list,
    coverage: dict[int, str],
    task_specs: list,
    soldier_states: list,
) -> None:
    """Log a compact quality scorecard to stderr."""
    total = len(task_specs)
    covered = sum(1 for s in coverage.values() if s == "OK")

    # Fairness.
    hours: dict[int, float] = {}
    for s in soldier_states:
        hours[s.id] = 0.0
    for a in assignments:
        hours[a.soldier_id] = hours.get(a.soldier_id, 0.0) + (
            a.end_time - a.start_time
        ).total_seconds() / 3600
    vals = list(hours.values())
    avg = sum(vals) / len(vals) if vals else 0
    mn = min(vals) if vals else 0
    mx = max(vals) if vals else 0
    spread = mx - mn

    # Rest gaps.
    from collections import defaultdict as _dd
    by_soldier: dict[int, list] = _dd(list)
    for a in assignments:
        by_soldier[a.soldier_id].append(a)

    back_to_back = 0
    short_2h = 0
    shortest = float("inf")
    for sid, asgns in by_soldier.items():
        asgns.sort(key=lambda x: x.start_time)
        for i in range(1, len(asgns)):
            gap_h = (asgns[i].start_time - asgns[i - 1].end_time).total_seconds() / 3600
            if abs(gap_h) < 0.02:
                back_to_back += 1
            elif gap_h < 2.0:
                short_2h += 1
            if gap_h < shortest:
                shortest = gap_h

    # Wakeups.
    total_wakeups = 0
    multi_wakeup = 0
    for sid, asgns in by_soldier.items():
        night_asgns = sorted(
            [a for a in asgns if a.start_time.hour >= 23 or a.start_time.hour < 7],
            key=lambda x: x.start_time,
        )
        if not night_asgns:
            continue
        groups = 1
        for i in range(1, len(night_asgns)):
            if night_asgns[i].start_time > night_asgns[i - 1].end_time:
                groups += 1
        total_wakeups += groups
        if groups >= 2:
            multi_wakeup += 1

    shortest_str = f"{shortest:.1f}h" if shortest != float("inf") else "N/A"
    _log(f"Schedule quality:")
    _log(f"  Coverage: {covered}/{total} tasks")
    _log(f"  Fairness: avg={avg:.1f}h spread={spread:.1f}h (min={mn:.1f} max={mx:.1f})")
    _log(f"  Rest gaps: {back_to_back} back-to-back, {short_2h} gaps<2h, shortest={shortest_str}")
    _log(f"  Night: {total_wakeups} wakeups, {multi_wakeup} soldiers with 2+")
