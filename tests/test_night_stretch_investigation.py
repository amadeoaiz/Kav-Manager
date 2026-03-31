"""
Investigation: Cost anatomy of the 75min night config with overlapping tasks.

Goal: Understand exactly which costs make "second block" expensive, and whether
120min blocks are cheaper overall despite larger individual blocks.

Updated for unified proximity penalty (replaces stretch + rest gap).
"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta

import pytest

from src.core.engine import FrozenAssignment, PlannedAssignment, SoldierState, TaskSpec
from src.core.lp_solver import (
    lp_solve, _generate_blocks_for_period, _map_tasks_to_blocks,
    _night_gap_penalty_factor,
)
from src.core.lp_weights import LPWeights
from src.core.models import PresenceInterval

logger = logging.getLogger(__name__)


_SOLDIER_DATA = [
    (1,  "Eshel",    ["Driver", "Explosives", "Observer", "Officer", "Sargent", "Squad Commander"], -0.111, 0.057),
    (2,  "Nadav",    ["Driver", "Explosives", "Kala", "Medic", "Sargent"], 0.060, 0.045),
    (3,  "Amadeo",   ["Kavan-Gil", "Magist", "Mashak-Gil", "Navigator"], 0.030, 0.214),
    (4,  "Shaul",    ["Driver", "Kavan-Gil", "Magist", "Mashak-Gil", "Negevist"], -0.084, -0.041),
    (5,  "Zimna",    ["Kavan-Gil", "Mashak-Gil", "Sargent", "Squad Commander"], 0.217, 0.089),
    (7,  "Kaplun",   ["Driver", "Explosives", "Sargent", "Squad Commander"], -0.423, -0.255),
    (8,  "Hillel",   ["Kala", "Kavan-Gil", "Mashak-Gil", "Navigator"], 0.225, 0.123),
    (9,  "Yuval",    ["Kala", "Observer", "Regular Drone Operator"], -0.423, -0.255),
    (10, "Kerten",   ["Explosives", "Kala", "Kavan-Gil", "Mashak-Gil", "Negevist"], 0.093, 0.078),
    (11, "Gabbay",   ["Kala", "Kavan-Gil", "Mashak-Gil", "Medic"], -0.147, -0.150),
    (12, "Malka",    ["Banai", "Driver", "Magist"], -0.120, -0.134),
    (15, "Gohar",    ["Kavan-Gil", "Mashak-Gil", "Negevist"], 0.296, 0.182),
    (16, "Tabacman", ["Matolist", "Observer"], 0.160, -0.033),
    (17, "Noiman",   ["Medic", "Squad Commander"], -0.188, -0.138),
]

def _parse_dt(s):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: {s}")


def _build_soldiers():
    soldiers = []
    for sid, name, roles, day_exc, night_exc in _SOLDIER_DATA:
        soldiers.append(SoldierState(
            id=sid, roles=roles, is_active=True,
            presence_intervals=[
                PresenceInterval(soldier_id=sid, start_time=_parse_dt("2026-03-22 00:00"),
                                 end_time=_parse_dt("2026-03-22 23:59:59"), status="PRESENT"),
                PresenceInterval(soldier_id=sid, start_time=_parse_dt("2026-03-23 00:00"),
                                 end_time=_parse_dt("2026-03-23 23:59:59"), status="PRESENT"),
            ],
            day_points=day_exc, night_points=night_exc,
        ))
    return soldiers


def _build_concurrent_night_tasks():
    d = datetime(2026, 3, 22)
    d_next = d + timedelta(days=1)
    return [
        TaskSpec(
            id=100, real_title="Shmirat Laila",
            start_time=d.replace(hour=22, minute=45), end_time=d_next.replace(hour=8),
            is_fractionable=True, is_night=True,
            required_roles=["Soldier"], concurrent_required=2,
            hardness=3, min_block_minutes=60, readiness_minutes=0,
            include_commander=True,
        ),
        TaskSpec(
            id=101, real_title="Jamal Laila",
            start_time=d.replace(hour=23), end_time=d_next.replace(hour=8),
            is_fractionable=True, is_night=True,
            required_roles=["Soldier"], concurrent_required=1,
            hardness=1, min_block_minutes=60, readiness_minutes=0,
            include_commander=True,
        ),
    ]


class TestCostAnatomy:
    """Detailed cost breakdown for 75min vs 120min night configs."""

    def test_75min_cost_anatomy(self):
        """For 75min config: arithmetic, then itemized costs for a second-block soldier."""
        tasks = _build_concurrent_night_tasks()
        w = LPWeights()

        night_start = datetime(2026, 3, 22, 23, 0)
        night_end = datetime(2026, 3, 23, 7, 0)

        blocks_75 = _generate_blocks_for_period(
            night_start, night_end, 75, is_night=True,
            night_start_hour=23, night_end_hour=7,
        )
        _map_tasks_to_blocks(blocks_75, tasks, min_overlap_minutes=30)

        print("\n" + "=" * 80)
        print("75min CONFIG — BLOCK LAYOUT")
        print("=" * 80)
        for b in blocks_75:
            task_ids = [tid for tid, _ in b.active_tasks]
            conc = sum(c for _, c in b.active_tasks)
            print(f"  Block {b.block_id}: {b.start_time.strftime('%H:%M')}→"
                  f"{b.end_time.strftime('%H:%M')} ({b.duration_minutes:.0f}min) "
                  f"tier={b.night_quality_tier} tasks={task_ids} req={conc}")

        n_blocks = len(blocks_75)
        total_conc = sum(sum(c for _, c in b.active_tasks) for b in blocks_75)
        n_soldiers = len(_SOLDIER_DATA)

        print(f"\n  COVERAGE ARITHMETIC:")
        print(f"    Blocks: {n_blocks}")
        print(f"    Total block-slots: {total_conc}")
        print(f"    Available soldiers: {n_soldiers}")
        print(f"    Soldiers needing 2 blocks: {total_conc - n_soldiers}")

        b0 = blocks_75[0]
        b4 = blocks_75[4]
        gap_h = (b4.start_time - b0.end_time).total_seconds() / 3600.0

        print(f"\n  SCENARIO: Soldier assigned to block 0 + block {b4.block_id}")
        print(f"    Block 0: {b0.start_time.strftime('%H:%M')}→{b0.end_time.strftime('%H:%M')} "
              f"({b0.duration_minutes:.0f}min)")
        print(f"    Block {b4.block_id}: {b4.start_time.strftime('%H:%M')}→"
              f"{b4.end_time.strftime('%H:%M')} ({b4.duration_minutes:.0f}min)")
        print(f"    Gap: {gap_h:.1f}h")

        # Proximity penalty (unified curve, replaces old stretch + rest gap)
        gap_factor = _night_gap_penalty_factor(gap_h)
        prox_cost_spaced = w.w_night_proximity * gap_factor
        print(f"\n  PROXIMITY COST (spaced, gap={gap_h:.1f}h):")
        print(f"    factor({gap_h:.1f}h) = {gap_factor}")
        print(f"    w_night_proximity × factor = {w.w_night_proximity} × {gap_factor} = {prox_cost_spaced:.1f}")

        # Adjacent blocks (gap=0)
        adj_factor = _night_gap_penalty_factor(0.0)
        prox_cost_adj = w.w_night_proximity * adj_factor
        print(f"\n  PROXIMITY COST (adjacent, gap=0h):")
        print(f"    factor(0h) = {adj_factor}")
        print(f"    w_night_proximity × factor = {w.w_night_proximity} × {adj_factor} = {prox_cost_adj:.1f}")
        print(f"\n  Adjacent ({prox_cost_adj:.0f}) vs spaced ({prox_cost_spaced:.0f}): "
              f"adjacent is {prox_cost_adj/max(prox_cost_spaced,0.01):.1f}× more expensive")

    def test_proximity_curve_comparison(self):
        """Show old system vs new system costs for key scenarios."""
        w = LPWeights()
        print("\n" + "=" * 80)
        print("PROXIMITY CURVE — cost at various gaps (night)")
        print("=" * 80)
        for gap in [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 5.5, 6.0, 7.0]:
            factor = _night_gap_penalty_factor(gap)
            cost = w.w_night_proximity * factor
            print(f"  gap={gap:4.1f}h: factor={factor:5.1f}, cost={cost:6.1f}")

    def test_concurrent_tasks_max_stretch(self, caplog):
        """Run solver with concurrent tasks and verify max stretch."""
        soldiers = _build_soldiers()
        tasks = _build_concurrent_night_tasks()
        ledger = {sid: {"day_points": de, "night_points": ne}
                  for sid, _, _, de, ne in _SOLDIER_DATA}

        sol = lp_solve(
            soldier_states=soldiers,
            task_specs=tasks,
            frozen_assignments=[],
            freeze_point=datetime(2026, 3, 22, 22, 0),
            night_start_hour=23,
            night_end_hour=7,
            weights=LPWeights(),
            effective_ledger=ledger,
        )

        by_soldier = defaultdict(list)
        for a in sol.assignments:
            by_soldier[a.soldier_id].append(a)

        name_by_id = {sid: name for sid, name, *_ in _SOLDIER_DATA}

        print("\n" + "=" * 80)
        print(f"WINNING CONFIG ANALYSIS")
        print("=" * 80)

        max_stretch = 0.0
        for sid, asgs in sorted(by_soldier.items()):
            asgs.sort(key=lambda a: a.start_time)
            name = name_by_id.get(sid, f"S{sid}")
            total_h = sum((a.end_time - a.start_time).total_seconds() / 3600 for a in asgs)

            stretches = []
            cs, ce = asgs[0].start_time, asgs[0].end_time
            for a in asgs[1:]:
                if (a.start_time - ce).total_seconds() <= 60:
                    ce = max(ce, a.end_time)
                else:
                    stretches.append((cs, ce))
                    cs, ce = a.start_time, a.end_time
            stretches.append((cs, ce))

            longest = max((e - s).total_seconds() / 3600 for s, e in stretches)
            max_stretch = max(max_stretch, longest)
            block_strs = [f"{a.start_time.strftime('%H:%M')}→{a.end_time.strftime('%H:%M')}"
                          for a in asgs]
            print(f"  {name:12s}: {len(asgs)} blocks, {total_h:.1f}h, "
                  f"longest_stretch={longest:.1f}h  [{', '.join(block_strs)}]")

        print(f"\n  Max stretch: {max_stretch:.1f}h")

        # With the unified proximity curve, adjacent blocks are the MOST expensive.
        # The solver should strongly prefer spacing blocks apart.
        assert max_stretch <= 2.5, (
            f"Max stretch {max_stretch:.1f}h > 2.5h — proximity penalty "
            f"should prevent long continuous stretches"
        )
