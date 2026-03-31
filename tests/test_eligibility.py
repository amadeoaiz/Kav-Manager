"""
Tests for per-task soldier exclusion and chain-of-command eligibility filtering.

Tests call lp_solve() directly and resolve_active_commander() to verify
that the eligibility filters work correctly at the LP variable creation level.
"""
from datetime import datetime, timedelta
from collections import defaultdict

import pytest

from src.core.engine import SoldierState, TaskSpec, FrozenAssignment
from src.core.lp_solver import lp_solve, _merge_present_intervals
from src.core.lp_weights import LPWeights
from src.core.models import PresenceInterval
from src.domain.command_rules import resolve_active_commander


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
          readiness=0, roles=None, hardness=3, excluded=None,
          include_commander=False):
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
        excluded_soldier_ids=excluded or [],
        include_commander=include_commander,
    )


def _solve(soldiers, tasks, frozen=None, ledger=None, weights=None,
           command_chain=None):
    return lp_solve(
        soldier_states=soldiers,
        task_specs=tasks,
        frozen_assignments=frozen or [],
        freeze_point=BASE,
        night_start_hour=NIGHT_START,
        night_end_hour=NIGHT_END,
        weights=weights or LPWeights(),
        effective_ledger=ledger or {},
        command_chain=command_chain or [],
    )


def _presence_map(soldiers):
    """Build soldier_id → merged presence intervals for commander resolution."""
    return {s.id: _merge_present_intervals(s) for s in soldiers}


# ══════════════════════════════════════════════════════════════════
# resolve_active_commander unit tests
# ══════════════════════════════════════════════════════════════════

class TestResolveActiveCommander:
    def _all_day(self):
        """Presence covering the full day."""
        return [(BASE + timedelta(hours=6), BASE + timedelta(hours=22))]

    def test_all_present_returns_primary(self):
        """Chain [A, B, C], all present → returns A."""
        pres = {1: self._all_day(), 2: self._all_day(), 3: self._all_day()}
        at = BASE + timedelta(hours=10)
        assert resolve_active_commander([1, 2, 3], pres, at) == 1

    def test_primary_absent_returns_secondary(self):
        """Chain [A, B, C], A absent → returns B."""
        pres = {1: [], 2: self._all_day(), 3: self._all_day()}
        at = BASE + timedelta(hours=10)
        assert resolve_active_commander([1, 2, 3], pres, at) == 2

    def test_primary_secondary_absent_returns_tertiary(self):
        """Chain [A, B, C], A and B absent → returns C."""
        pres = {1: [], 2: [], 3: self._all_day()}
        at = BASE + timedelta(hours=10)
        assert resolve_active_commander([1, 2, 3], pres, at) == 3

    def test_all_absent_returns_none(self):
        """Chain [A, B, C], all absent → returns None."""
        pres = {1: [], 2: [], 3: []}
        at = BASE + timedelta(hours=10)
        assert resolve_active_commander([1, 2, 3], pres, at) is None

    def test_empty_chain_returns_none(self):
        """Empty chain → returns None."""
        pres = {1: self._all_day()}
        at = BASE + timedelta(hours=10)
        assert resolve_active_commander([], pres, at) is None

    def test_single_entry_chain(self):
        """Chain [A], A present → returns A."""
        pres = {1: self._all_day()}
        at = BASE + timedelta(hours=10)
        assert resolve_active_commander([1], pres, at) == 1

    def test_presence_boundary(self):
        """A present until 14:00, B present all day.
        Before 14:00 → A. At 14:00 → B (A's interval ends at 14:00, half-open)."""
        pres = {
            1: [(BASE + timedelta(hours=6), BASE + timedelta(hours=14))],
            2: [(BASE + timedelta(hours=6), BASE + timedelta(hours=22))],
        }
        # Before boundary: A is commander.
        assert resolve_active_commander([1, 2], pres, BASE + timedelta(hours=13)) == 1
        # At boundary: A's interval is [6,14), so at 14:00 A is absent → B.
        assert resolve_active_commander([1, 2], pres, BASE + timedelta(hours=14)) == 2


# ══════════════════════════════════════════════════════════════════
# Per-task exclusion LP tests
# ══════════════════════════════════════════════════════════════════

class TestPerTaskExclusion:
    def test_excluded_soldier_not_assigned(self):
        """Excluded soldier never appears in assignments for that task."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                     excluded=[1, 2])
        sol = _solve(soldiers, [task])
        for a in sol.assignments:
            assert a.soldier_id not in (1, 2), (
                f"Excluded soldier {a.soldier_id} was assigned to task 1"
            )

    def test_excluded_soldier_can_be_assigned_elsewhere(self):
        """Excluded soldier CAN be assigned to other tasks."""
        soldiers = [_soldier(i) for i in range(1, 4)]
        t1 = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                   excluded=[1])
        t2 = _task(2, BASE + timedelta(hours=10), BASE + timedelta(hours=14))
        sol = _solve(soldiers, [t1, t2])
        # Soldier 1 should NOT be in task 1's assignments.
        for a in sol.assignments:
            if a.task_id == 1:
                assert a.soldier_id != 1
        # Soldier 1 CAN appear in task 2.
        t2_soldiers = {a.soldier_id for a in sol.assignments if a.task_id == 2}
        assert 1 in t2_soldiers, "Excluded soldier should still be eligible for other tasks"

    def test_excluding_all_eligible_soldiers(self):
        """Excluding all eligible soldiers → task UNCOVERED, no crash."""
        soldiers = [_soldier(1), _soldier(2)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                     excluded=[1, 2])
        sol = _solve(soldiers, [task])
        assert sol.coverage_status[1] == "UNCOVERED"

    def test_empty_exclusion_list_no_regression(self):
        """Empty exclusion list → same behavior as before."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                     excluded=[])
        sol = _solve(soldiers, [task])
        assert sol.coverage_status[1] == "OK"
        assert len(sol.assignments) >= 1


# ══════════════════════════════════════════════════════════════════
# Commander exclusion LP tests
# ══════════════════════════════════════════════════════════════════

class TestCommanderExclusion:
    def test_commander_excluded_by_default(self):
        """Task with include_commander=False, commander present → not assigned."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                     include_commander=False)
        sol = _solve(soldiers, [task], command_chain=[1, 2, 3])
        for a in sol.assignments:
            assert a.soldier_id != 1, (
                "Commander (soldier 1) should be excluded from task"
            )

    def test_commander_included_when_opted_in(self):
        """Task with include_commander=True → commander can be assigned."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                     include_commander=True)
        sol = _solve(soldiers, [task], command_chain=[1, 2, 3])
        # Commander is allowed — just verify no crash and task is covered.
        assert sol.coverage_status[1] == "OK"

    def test_commander_fallback(self):
        """Primary absent, secondary present → secondary excluded."""
        # Soldier 1 (primary) is absent for the task window.
        s1 = SoldierState(
            id=1, roles=[], is_active=True,
            presence_intervals=[],  # absent
            day_points=0.0, night_points=0.0,
        )
        soldiers = [s1] + [_soldier(i) for i in range(2, 8)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                     include_commander=False)
        sol = _solve(soldiers, [task], command_chain=[1, 2, 3])
        # Soldier 2 (secondary) becomes active commander → excluded.
        for a in sol.assignments:
            assert a.soldier_id != 2, (
                "Secondary commander (soldier 2) should be excluded when primary is absent"
            )

    def test_mid_task_commander_change(self):
        """Primary arrives at 14:00. Before 14:00 secondary is commander,
        after 14:00 primary is commander."""
        # Primary: present 14:00–22:00 only.
        s1 = SoldierState(
            id=1, roles=[], is_active=True,
            presence_intervals=[_presence(
                BASE + timedelta(hours=14),
                BASE + timedelta(hours=22),
            )],
            day_points=0.0, night_points=0.0,
        )
        # Others: present all day.
        soldiers = [s1] + [_soldier(i) for i in range(2, 8)]
        # Task spans 08:00–20:00.
        task = _task(1, BASE + timedelta(hours=8), BASE + timedelta(hours=20),
                     include_commander=False)
        sol = _solve(soldiers, [task], command_chain=[1, 2])

        # Blocks before 14:00: secondary (2) should be excluded.
        # Blocks after 14:00: primary (1) should be excluded.
        for a in sol.assignments:
            mid = a.start_time + (a.end_time - a.start_time) / 2
            if mid < BASE + timedelta(hours=14):
                assert a.soldier_id != 2, (
                    f"Secondary should be excluded before 14:00, got {a.soldier_id} "
                    f"at {a.start_time}-{a.end_time}"
                )
            else:
                assert a.soldier_id != 1, (
                    f"Primary should be excluded after 14:00, got {a.soldier_id} "
                    f"at {a.start_time}-{a.end_time}"
                )

    def test_empty_chain_no_exclusion(self):
        """include_commander=False but chain is empty → no one excluded."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                     include_commander=False)
        sol = _solve(soldiers, [task], command_chain=[])
        assert sol.coverage_status[1] == "OK"

    def test_commander_and_exclusion_list_interaction(self):
        """Soldier is both active commander AND in exclusion list → still excluded."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                     include_commander=False, excluded=[1])
        sol = _solve(soldiers, [task], command_chain=[1, 2, 3])
        for a in sol.assignments:
            assert a.soldier_id != 1


# ══════════════════════════════════════════════════════════════════
# Fixed (non-fractionable) task exclusion tests
# ══════════════════════════════════════════════════════════════════

class TestFixedTaskExclusion:
    def test_excluded_from_fixed_task(self):
        """Excluded soldier not assigned to fixed task."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=12),
                     fractionable=False, excluded=[1, 2])
        sol = _solve(soldiers, [task])
        for a in sol.assignments:
            assert a.soldier_id not in (1, 2)

    def test_commander_excluded_from_fixed_task(self):
        """Commander excluded from fixed task when include_commander=False."""
        soldiers = [_soldier(i) for i in range(1, 6)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=12),
                     fractionable=False, include_commander=False)
        sol = _solve(soldiers, [task], command_chain=[1])
        for a in sol.assignments:
            assert a.soldier_id != 1


# ══════════════════════════════════════════════════════════════════
# Integration tests (both features together)
# ══════════════════════════════════════════════════════════════════

class TestEligibilityIntegration:
    def test_exclusion_plus_commander_both_apply(self):
        """Task with exclusion list + commander exclusion: both filters apply."""
        soldiers = [_soldier(i) for i in range(1, 8)]
        # Exclude soldiers 3,4 via list; soldier 1 is commander.
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                     include_commander=False, excluded=[3, 4])
        sol = _solve(soldiers, [task], command_chain=[1])
        for a in sol.assignments:
            assert a.soldier_id not in (1, 3, 4), (
                f"Soldier {a.soldier_id} should be excluded"
            )
        assert sol.coverage_status[1] == "OK"

    def test_commander_exclusion_makes_task_uncoverable(self):
        """If commander exclusion leaves no eligible soldiers → UNCOVERED."""
        soldiers = [_soldier(1), _soldier(2)]
        task = _task(1, BASE + timedelta(hours=10), BASE + timedelta(hours=14),
                     include_commander=False, excluded=[2])
        # Soldier 1 is commander (excluded), soldier 2 is in exclusion list.
        sol = _solve(soldiers, [task], command_chain=[1])
        assert sol.coverage_status[1] == "UNCOVERED"
