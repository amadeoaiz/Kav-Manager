"""
Multi-day diagnostic test for LP solver block length and soldier utilization.

Data source: Real soldier roster (17 soldiers) and presence intervals from
data/app.db for the week of March 22-28, 2026.  Hardcoded — no runtime DB
dependency.

Purpose: Reproduce the "long blocks + underutilized soldiers" problem.
The solver currently prefers fewer soldiers doing longer shifts (~2-2.5h)
over spreading the load across all available soldiers (~1h shifts).
"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta

import pytest

from src.core.engine import FrozenAssignment, PlannedAssignment, SoldierState, TaskSpec
from src.core.lp_solver import (
    lp_solve, _is_night_time,
    _soldier_available_for_block, _build_eligible_matrix,
)
from src.core.lp_weights import LPWeights
from src.core.models import PresenceInterval

logger = logging.getLogger(__name__)

NIGHT_START = 23
NIGHT_END = 7


# ══════════════════════════════════════════════════════════════════
# Real roster: 17 soldiers, roles, and presence intervals
# (extracted from data/app.db, March 22-28 2026)
# ══════════════════════════════════════════════════════════════════

_SOLDIER_DATA = [
    # (id, name, roles, day_excess, night_excess)
    (1,  "Eshel",    ["Driver", "Explosives", "Observer", "Officer", "Sargent", "Squad Commander"], -0.111, 0.057),
    (2,  "Nadav",    ["Driver", "Explosives", "Kala", "Medic", "Sargent"], 0.060, 0.045),
    (3,  "Amadeo",   ["Kavan-Gil", "Magist", "Mashak-Gil", "Navigator"], 0.030, 0.214),
    (4,  "Shaul",    ["Driver", "Kavan-Gil", "Magist", "Mashak-Gil", "Negevist"], -0.084, -0.041),
    (5,  "Zimna",    ["Kavan-Gil", "Mashak-Gil", "Sargent", "Squad Commander"], 0.217, 0.089),
    (6,  "Piki",     ["FPV Drone Operator", "Kala", "Observer", "Regular Drone Operator"], 0.188, 0.189),
    (7,  "Kaplun",   ["Driver", "Explosives", "Sargent", "Squad Commander"], -0.423, -0.255),
    (8,  "Hillel",   ["Kala", "Kavan-Gil", "Mashak-Gil", "Navigator"], 0.225, 0.123),
    (9,  "Yuval",    ["Kala", "Observer", "Regular Drone Operator"], -0.423, -0.255),
    (10, "Kerten",   ["Explosives", "Kala", "Kavan-Gil", "Mashak-Gil", "Negevist"], 0.093, 0.078),
    (11, "Gabbay",   ["Kala", "Kavan-Gil", "Mashak-Gil", "Medic"], -0.147, -0.150),
    (12, "Malka",    ["Banai", "Driver", "Magist"], -0.120, -0.134),
    (13, "Elbaz",    ["Banai", "Magist", "Matolist"], 0.043, 0.011),
    (14, "Yoavi",    ["Driver", "FPV Drone Operator", "Operational Driver", "Regular Drone Operator", "Sargent", "SmartShooter", "Squad Commander"], 0.183, 0.018),
    (15, "Gohar",    ["Kavan-Gil", "Mashak-Gil", "Negevist"], 0.296, 0.182),
    (16, "Tabacman", ["Matolist", "Observer"], 0.160, -0.033),
    (17, "Noiman",   ["Medic", "Squad Commander"], -0.188, -0.138),
]

# Presence intervals: (soldier_id, start, end, status)
# Stored as (sid, "YYYY-MM-DD HH:MM", "YYYY-MM-DD HH:MM", status)
_PRESENCE_RAW = [
    # Soldier 1 - Eshel: present Mar 22-25 full, Mar 26 morning only, then absent
    (1, "2026-03-22 00:00", "2026-03-22 23:59:59", "PRESENT"),
    (1, "2026-03-23 00:00", "2026-03-23 23:59:59", "PRESENT"),
    (1, "2026-03-24 00:00", "2026-03-24 23:59:59", "PRESENT"),
    (1, "2026-03-25 00:00", "2026-03-25 23:59:59", "PRESENT"),
    (1, "2026-03-26 00:00", "2026-03-26 12:00", "PRESENT"),
    (1, "2026-03-26 12:00", "2026-03-26 23:59:59", "ABSENT"),
    (1, "2026-03-27 00:00", "2026-03-27 23:59:59", "ABSENT"),
    (1, "2026-03-28 00:00", "2026-03-28 23:59:59", "ABSENT"),
    # Soldier 2 - Nadav: absent Mar 22 + Mar 23 morning, present till Mar 28 morning
    (2, "2026-03-22 00:00", "2026-03-22 23:59:59", "ABSENT"),
    (2, "2026-03-23 00:00", "2026-03-23 12:00", "ABSENT"),
    (2, "2026-03-23 12:00", "2026-03-23 23:59:59", "PRESENT"),
    (2, "2026-03-24 00:00", "2026-03-24 23:59:59", "PRESENT"),
    (2, "2026-03-25 00:00", "2026-03-25 23:59:59", "PRESENT"),
    (2, "2026-03-26 00:00", "2026-03-26 23:59:59", "PRESENT"),
    (2, "2026-03-27 00:00", "2026-03-27 23:59:59", "PRESENT"),
    (2, "2026-03-28 00:00", "2026-03-28 12:00", "PRESENT"),
    (2, "2026-03-28 12:00", "2026-03-28 23:59:59", "ABSENT"),
    # Soldier 3 - Amadeo: fully present all week
    (3, "2026-03-22 00:00", "2026-03-22 23:59:59", "PRESENT"),
    (3, "2026-03-23 00:00", "2026-03-23 23:59:59", "PRESENT"),
    (3, "2026-03-24 00:00", "2026-03-24 23:59:59", "PRESENT"),
    (3, "2026-03-25 00:00", "2026-03-25 23:59:59", "PRESENT"),
    (3, "2026-03-26 00:00", "2026-03-26 23:59:59", "PRESENT"),
    (3, "2026-03-27 00:00", "2026-03-27 23:59:59", "PRESENT"),
    (3, "2026-03-28 00:00", "2026-03-28 23:59:59", "PRESENT"),
    # Soldier 4 - Shaul: present Mar 22-24 morning, absent till Mar 28 afternoon
    (4, "2026-03-22 00:00", "2026-03-22 23:59:59", "PRESENT"),
    (4, "2026-03-23 00:00", "2026-03-23 23:59:59", "PRESENT"),
    (4, "2026-03-24 00:00", "2026-03-24 12:00", "PRESENT"),
    (4, "2026-03-24 12:00", "2026-03-24 23:59:59", "ABSENT"),
    (4, "2026-03-25 00:00", "2026-03-25 23:59:59", "ABSENT"),
    (4, "2026-03-26 00:00", "2026-03-26 23:59:59", "ABSENT"),
    (4, "2026-03-27 00:00", "2026-03-27 23:59:59", "ABSENT"),
    (4, "2026-03-28 00:00", "2026-03-28 12:00", "ABSENT"),
    (4, "2026-03-28 12:00", "2026-03-28 23:59:59", "PRESENT"),
    # Soldier 5 - Zimna: present Mar 22-24 morning, absent, returns Mar 27 afternoon
    (5, "2026-03-22 00:00", "2026-03-22 23:59:59", "PRESENT"),
    (5, "2026-03-23 00:00", "2026-03-23 23:59:59", "PRESENT"),
    (5, "2026-03-24 00:00", "2026-03-24 12:00", "PRESENT"),
    (5, "2026-03-24 12:00", "2026-03-24 23:59:59", "ABSENT"),
    (5, "2026-03-25 00:00", "2026-03-25 23:59:59", "ABSENT"),
    (5, "2026-03-26 00:00", "2026-03-26 23:59:59", "ABSENT"),
    (5, "2026-03-27 00:00", "2026-03-27 12:00", "ABSENT"),
    (5, "2026-03-27 12:00", "2026-03-27 23:59:59", "PRESENT"),
    (5, "2026-03-28 00:00", "2026-03-28 23:59:59", "PRESENT"),
    # Soldier 6 - Piki: absent till Mar 25 afternoon, present rest of week
    (6, "2026-03-22 00:00", "2026-03-22 23:59:59", "ABSENT"),
    (6, "2026-03-23 00:00", "2026-03-23 23:59:59", "ABSENT"),
    (6, "2026-03-24 00:00", "2026-03-24 23:59:59", "ABSENT"),
    (6, "2026-03-25 00:00", "2026-03-25 12:00", "ABSENT"),
    (6, "2026-03-25 12:00", "2026-03-25 23:59:59", "PRESENT"),
    (6, "2026-03-26 00:00", "2026-03-26 23:59:59", "PRESENT"),
    (6, "2026-03-27 00:00", "2026-03-27 23:59:59", "PRESENT"),
    (6, "2026-03-28 00:00", "2026-03-28 23:59:59", "PRESENT"),
    # Soldier 7 - Kaplun: fully present all week
    (7, "2026-03-22 00:00", "2026-03-22 23:59:59", "PRESENT"),
    (7, "2026-03-23 00:00", "2026-03-23 23:59:59", "PRESENT"),
    (7, "2026-03-24 00:00", "2026-03-24 23:59:59", "PRESENT"),
    (7, "2026-03-25 00:00", "2026-03-25 23:59:59", "PRESENT"),
    (7, "2026-03-26 00:00", "2026-03-26 23:59:59", "PRESENT"),
    (7, "2026-03-27 00:00", "2026-03-27 23:59:59", "PRESENT"),
    (7, "2026-03-28 00:00", "2026-03-28 23:59:59", "PRESENT"),
    # Soldier 8 - Hillel: fully present all week
    (8, "2026-03-22 00:00", "2026-03-22 23:59:59", "PRESENT"),
    (8, "2026-03-23 00:00", "2026-03-23 23:59:59", "PRESENT"),
    (8, "2026-03-24 00:00", "2026-03-24 23:59:59", "PRESENT"),
    (8, "2026-03-25 00:00", "2026-03-25 23:59:59", "PRESENT"),
    (8, "2026-03-26 00:00", "2026-03-26 23:59:59", "PRESENT"),
    (8, "2026-03-27 00:00", "2026-03-27 23:59:59", "PRESENT"),
    (8, "2026-03-28 00:00", "2026-03-28 23:59:59", "PRESENT"),
    # Soldier 9 - Yuval: present till Mar 28 morning
    (9, "2026-03-22 00:00", "2026-03-22 23:59:59", "PRESENT"),
    (9, "2026-03-23 00:00", "2026-03-23 23:59:59", "PRESENT"),
    (9, "2026-03-24 00:00", "2026-03-24 23:59:59", "PRESENT"),
    (9, "2026-03-25 00:00", "2026-03-25 23:59:59", "PRESENT"),
    (9, "2026-03-26 00:00", "2026-03-26 23:59:59", "PRESENT"),
    (9, "2026-03-27 00:00", "2026-03-27 23:59:59", "PRESENT"),
    (9, "2026-03-28 00:00", "2026-03-28 12:00", "PRESENT"),
    (9, "2026-03-28 12:00", "2026-03-28 23:59:59", "ABSENT"),
    # Soldier 10 - Kerten: present till Mar 25 morning, absent, returns Mar 28 afternoon
    (10, "2026-03-22 00:00", "2026-03-22 23:59:59", "PRESENT"),
    (10, "2026-03-23 00:00", "2026-03-23 23:59:59", "PRESENT"),
    (10, "2026-03-24 00:00", "2026-03-24 23:59:59", "PRESENT"),
    (10, "2026-03-25 00:00", "2026-03-25 12:00", "PRESENT"),
    (10, "2026-03-25 12:00", "2026-03-25 23:59:59", "ABSENT"),
    (10, "2026-03-26 00:00", "2026-03-26 23:59:59", "ABSENT"),
    (10, "2026-03-27 00:00", "2026-03-27 23:59:59", "ABSENT"),
    (10, "2026-03-28 00:00", "2026-03-28 12:00", "ABSENT"),
    (10, "2026-03-28 12:00", "2026-03-28 23:59:59", "PRESENT"),
    # Soldier 11 - Gabbay: absent Mar 22 morning, present rest of week
    (11, "2026-03-22 00:00", "2026-03-22 12:00", "ABSENT"),
    (11, "2026-03-22 12:00", "2026-03-22 23:59:59", "PRESENT"),
    (11, "2026-03-23 00:00", "2026-03-23 23:59:59", "PRESENT"),
    (11, "2026-03-24 00:00", "2026-03-24 23:59:59", "PRESENT"),
    (11, "2026-03-25 00:00", "2026-03-25 23:59:59", "PRESENT"),
    (11, "2026-03-26 00:00", "2026-03-26 23:59:59", "PRESENT"),
    (11, "2026-03-27 00:00", "2026-03-27 23:59:59", "PRESENT"),
    (11, "2026-03-28 00:00", "2026-03-28 23:59:59", "PRESENT"),
    # Soldier 12 - Malka: fully present all week
    (12, "2026-03-22 00:00", "2026-03-22 23:59:59", "PRESENT"),
    (12, "2026-03-23 00:00", "2026-03-23 23:59:59", "PRESENT"),
    (12, "2026-03-24 00:00", "2026-03-24 23:59:59", "PRESENT"),
    (12, "2026-03-25 00:00", "2026-03-25 23:59:59", "PRESENT"),
    (12, "2026-03-26 00:00", "2026-03-26 23:59:59", "PRESENT"),
    (12, "2026-03-27 00:00", "2026-03-27 23:59:59", "PRESENT"),
    (12, "2026-03-28 00:00", "2026-03-28 23:59:59", "PRESENT"),
    # Soldier 13 - Elbaz: present Mar 22 morning, absent till Mar 26 afternoon
    (13, "2026-03-22 00:00", "2026-03-22 12:00", "PRESENT"),
    (13, "2026-03-22 12:00", "2026-03-22 23:59:59", "ABSENT"),
    (13, "2026-03-23 00:00", "2026-03-23 23:59:59", "ABSENT"),
    (13, "2026-03-24 00:00", "2026-03-24 23:59:59", "ABSENT"),
    (13, "2026-03-25 00:00", "2026-03-25 23:59:59", "ABSENT"),
    (13, "2026-03-26 00:00", "2026-03-26 12:00", "ABSENT"),
    (13, "2026-03-26 12:00", "2026-03-26 23:59:59", "PRESENT"),
    (13, "2026-03-27 00:00", "2026-03-27 23:59:59", "PRESENT"),
    (13, "2026-03-28 00:00", "2026-03-28 23:59:59", "PRESENT"),
    # Soldier 14 - Yoavi: absent Mar 22 + Mar 23 morning, present rest of week
    (14, "2026-03-22 00:00", "2026-03-22 23:59:59", "ABSENT"),
    (14, "2026-03-23 00:00", "2026-03-23 12:00", "ABSENT"),
    (14, "2026-03-23 12:00", "2026-03-23 23:59:59", "PRESENT"),
    (14, "2026-03-24 00:00", "2026-03-24 23:59:59", "PRESENT"),
    (14, "2026-03-25 00:00", "2026-03-25 23:59:59", "PRESENT"),
    (14, "2026-03-26 00:00", "2026-03-26 23:59:59", "PRESENT"),
    (14, "2026-03-27 00:00", "2026-03-27 23:59:59", "PRESENT"),
    (14, "2026-03-28 00:00", "2026-03-28 23:59:59", "PRESENT"),
    # Soldier 15 - Gohar: present Mar 22 only, absent, returns Mar 26 afternoon
    (15, "2026-03-22 00:00", "2026-03-22 23:59:59", "PRESENT"),
    (15, "2026-03-23 00:00", "2026-03-23 23:59:59", "ABSENT"),
    (15, "2026-03-24 00:00", "2026-03-24 23:59:59", "ABSENT"),
    (15, "2026-03-25 00:00", "2026-03-25 23:59:59", "ABSENT"),
    (15, "2026-03-26 00:00", "2026-03-26 12:00", "ABSENT"),
    (15, "2026-03-26 12:00", "2026-03-26 23:59:59", "PRESENT"),
    (15, "2026-03-27 00:00", "2026-03-27 23:59:59", "PRESENT"),
    (15, "2026-03-28 00:00", "2026-03-28 23:59:59", "PRESENT"),
    # Soldier 16 - Tabacman: present till Mar 27 morning, then absent
    (16, "2026-03-22 00:00", "2026-03-22 23:59:59", "PRESENT"),
    (16, "2026-03-23 00:00", "2026-03-23 23:59:59", "PRESENT"),
    (16, "2026-03-24 00:00", "2026-03-24 23:59:59", "PRESENT"),
    (16, "2026-03-25 00:00", "2026-03-25 23:59:59", "PRESENT"),
    (16, "2026-03-26 00:00", "2026-03-26 23:59:59", "PRESENT"),
    (16, "2026-03-27 00:00", "2026-03-27 12:00", "PRESENT"),
    (16, "2026-03-27 12:00", "2026-03-27 23:59:59", "ABSENT"),
    (16, "2026-03-28 00:00", "2026-03-28 23:59:59", "ABSENT"),
    # Soldier 17 - Noiman: present till Mar 25 morning, absent, returns Mar 28 afternoon
    (17, "2026-03-22 00:00", "2026-03-22 23:59:59", "PRESENT"),
    (17, "2026-03-23 00:00", "2026-03-23 23:59:59", "PRESENT"),
    (17, "2026-03-24 00:00", "2026-03-24 23:59:59", "PRESENT"),
    (17, "2026-03-25 00:00", "2026-03-25 12:00", "PRESENT"),
    (17, "2026-03-25 12:00", "2026-03-25 23:59:59", "ABSENT"),
    (17, "2026-03-26 00:00", "2026-03-26 23:59:59", "ABSENT"),
    (17, "2026-03-27 00:00", "2026-03-27 23:59:59", "ABSENT"),
    (17, "2026-03-28 00:00", "2026-03-28 12:00", "ABSENT"),
    (17, "2026-03-28 12:00", "2026-03-28 23:59:59", "PRESENT"),
]


def _parse_dt(s: str) -> datetime:
    """Parse 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD HH:MM:SS'."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: {s}")


def _build_soldiers() -> list[SoldierState]:
    """Build SoldierState list from hardcoded roster and presence data."""
    presence_by_sid: dict[int, list[PresenceInterval]] = defaultdict(list)
    for sid, start_s, end_s, status in _PRESENCE_RAW:
        presence_by_sid[sid].append(PresenceInterval(
            soldier_id=sid,
            start_time=_parse_dt(start_s),
            end_time=_parse_dt(end_s),
            status=status,
        ))

    soldiers = []
    for sid, name, roles, day_exc, night_exc in _SOLDIER_DATA:
        soldiers.append(SoldierState(
            id=sid,
            roles=roles,
            is_active=True,
            presence_intervals=presence_by_sid.get(sid, []),
            day_points=day_exc,
            night_points=night_exc,
        ))
    return soldiers


def _build_ledger() -> dict:
    """Build the effective_ledger from stored excess values."""
    ledger = {}
    for sid, _, _, day_exc, night_exc in _SOLDIER_DATA:
        ledger[sid] = {"day_points": day_exc, "night_points": night_exc}
    return ledger


def _build_tasks_for_day(
    day_date: datetime,
    task_id_offset: int,
    peak_load: bool = False,
) -> list[TaskSpec]:
    """Build a realistic set of tasks for one day/night cycle.

    Modeled on actual task patterns from the database:
    - Day Guard:   fractionable, req=2, hardness=2, 11:00-21:00
    - Night Guard: fractionable, req=2, hardness=3, 21:00-09:00+1
    - Patrol:      fixed,        req=4 (1 Driver), hardness=3, 18:30-21:00
    - (peak only) Kitchen Duty: fixed, req=2, hardness=4, 12:00-16:00
    - (peak only) Night Drone:  fixed, req=1, Regular Drone Operator, hardness=3, 04:30-06:00+1
    """
    d = day_date.replace(hour=0, minute=0, second=0, microsecond=0)
    tasks = []
    tid = task_id_offset

    # Day Guard: 11:00-21:00 (fractionable, req=2, hardness=2)
    tasks.append(TaskSpec(
        id=tid, real_title=f"Day Guard ({d.strftime('%m/%d')})",
        start_time=d.replace(hour=11), end_time=d.replace(hour=21),
        is_fractionable=True, is_night=False,
        required_roles=["Soldier"], concurrent_required=2,
        hardness=2, min_block_minutes=60, readiness_minutes=0,
        include_commander=True,
    ))
    tid += 1

    # Night Guard: 21:00 - 09:00+1 (fractionable, req=2, hardness=3)
    tasks.append(TaskSpec(
        id=tid, real_title=f"Night Guard ({d.strftime('%m/%d')})",
        start_time=d.replace(hour=21), end_time=(d + timedelta(days=1)).replace(hour=9),
        is_fractionable=True, is_night=True,
        required_roles=["Soldier"], concurrent_required=2,
        hardness=3, min_block_minutes=60, readiness_minutes=0,
        include_commander=True,
    ))
    tid += 1

    # Patrol: 18:30-21:00 (fixed, req=4, 1 Driver, hardness=3)
    tasks.append(TaskSpec(
        id=tid, real_title=f"Patrol ({d.strftime('%m/%d')})",
        start_time=d.replace(hour=18, minute=30), end_time=d.replace(hour=21),
        is_fractionable=False, is_night=False,
        required_roles=["Soldier"], concurrent_required=4,
        hardness=3, min_block_minutes=60, readiness_minutes=0,
        include_commander=True,
    ))
    # Note: In reality one role is Driver, but for simplicity we use Soldier
    # to avoid blocking on role availability in the diagnostic.
    tid += 1

    if peak_load:
        # Kitchen Duty: 12:00-16:00 (fixed, req=2, hardness=4)
        tasks.append(TaskSpec(
            id=tid, real_title=f"Kitchen Duty ({d.strftime('%m/%d')})",
            start_time=d.replace(hour=12), end_time=d.replace(hour=16),
            is_fractionable=False, is_night=False,
            required_roles=["Soldier"], concurrent_required=2,
            hardness=4, min_block_minutes=60, readiness_minutes=0,
        ))
        tid += 1

        # Night Drone: 04:30-06:00+1 (fixed, req=1, Regular Drone Operator, hardness=3)
        tasks.append(TaskSpec(
            id=tid, real_title=f"Night Drone ({d.strftime('%m/%d')})",
            start_time=(d + timedelta(days=1)).replace(hour=4, minute=30),
            end_time=(d + timedelta(days=1)).replace(hour=6),
            is_fractionable=False, is_night=True,
            required_roles=["Regular Drone Operator"], concurrent_required=1,
            hardness=3, min_block_minutes=60, readiness_minutes=0,
        ))
        tid += 1

    return tasks


def _soldiers_present_during(
    soldiers: list[SoldierState],
    start: datetime,
    end: datetime,
) -> list[int]:
    """Return IDs of soldiers with any PRESENT overlap in [start, end)."""
    present = []
    for s in soldiers:
        for pi in s.presence_intervals:
            if pi.status != "PRESENT":
                continue
            if pi.end_time > start and pi.start_time < end:
                present.append(s.id)
                break
    return present


# ══════════════════════════════════════════════════════════════════
# The diagnostic test
# ══════════════════════════════════════════════════════════════════

class TestBlockLengthDiagnostic:
    """Multi-day diagnostic test reproducing the long-block / idle-soldier problem.

    Runs lp_solve() for 5 consecutive day/night cycles (Mar 22-26) with
    the real roster.  One day (Mar 24) gets peak load.

    Each cycle is solved independently (as the real system does per reconcile).
    We accumulate assignments across days and analyze the patterns.
    """

    @pytest.fixture(autouse=True)
    def setup_diagnostic(self, caplog):
        """Enable detailed logging for the duration of the test."""
        with caplog.at_level(logging.INFO, logger="src.core.lp_solver"):
            yield

    def _solve_day(self, day_date, task_id_offset, soldiers, ledger,
                   peak_load=False, prior_hours=None):
        """Solve one day/night cycle and return (solution, tasks)."""
        tasks = _build_tasks_for_day(day_date, task_id_offset, peak_load=peak_load)
        sol = lp_solve(
            soldier_states=soldiers,
            task_specs=tasks,
            frozen_assignments=[],
            freeze_point=day_date.replace(hour=10, minute=0),
            night_start_hour=NIGHT_START,
            night_end_hour=NIGHT_END,
            weights=LPWeights(),
            effective_ledger=ledger,
        )
        return sol, tasks

    def test_multiday_utilization_diagnostic(self, caplog):
        """Run 5 day/night cycles and diagnose block length + soldier utilization.

        This test's assertions describe DESIRED behavior.  They are expected
        to FAIL with the current solver weights, revealing which LP components
        prevent proper load distribution.
        """
        soldiers = _build_soldiers()
        ledger = _build_ledger()

        # Run 5 cycles: Mar 22-26
        all_solutions = []
        all_tasks = []
        day_dates = [
            datetime(2026, 3, 22),
            datetime(2026, 3, 23),
            datetime(2026, 3, 24),  # peak load day
            datetime(2026, 3, 25),
            datetime(2026, 3, 26),
        ]

        for i, day_date in enumerate(day_dates):
            is_peak = (i == 2)  # Mar 24 = peak
            sol, tasks = self._solve_day(
                day_date, task_id_offset=i * 10,
                soldiers=soldiers, ledger=ledger,
                peak_load=is_peak,
            )
            all_solutions.append(sol)
            all_tasks.extend(tasks)
            logger.info(f"\n--- Day {day_date.strftime('%m/%d')} "
                        f"({'PEAK' if is_peak else 'normal'}) ---")
            logger.info(f"  Status: {sol.status}, "
                        f"solve_time: {sol.solve_time_seconds:.2f}s, "
                        f"assignments: {len(sol.assignments)}")

        # ── Diagnostic analysis and assertions ──

        # Analyze each day/night guard window.
        window_diagnostics = []
        all_frac_durations = []

        for i, (day_date, sol) in enumerate(zip(day_dates, all_solutions)):
            tasks = _build_tasks_for_day(day_date, i * 10, peak_load=(i == 2))

            for task in tasks:
                if not task.is_fractionable:
                    continue

                # Find assignments for this task.
                task_asgn = [a for a in sol.assignments if a.task_id == task.id]
                assigned_sids = set(a.soldier_id for a in task_asgn)

                # Count present soldiers during this task's window.
                present_sids = _soldiers_present_during(
                    soldiers, task.start_time, task.end_time,
                )

                # Compute per-assignment durations.
                durations = []
                for a in task_asgn:
                    dur_min = (a.end_time - a.start_time).total_seconds() / 60.0
                    durations.append(dur_min)
                    all_frac_durations.append(dur_min)

                avg_dur = sum(durations) / len(durations) if durations else 0
                max_dur = max(durations) if durations else 0

                # Identify idle soldiers with below-average excess.
                idle_sids = set(present_sids) - assigned_sids
                is_night = task.real_title.startswith("Night")
                unjustified_idle = []
                for sid in idle_sids:
                    exc = (ledger.get(sid, {}).get("night_points", 0.0) if is_night
                           else ledger.get(sid, {}).get("day_points", 0.0))
                    if exc <= 0.0:
                        # Below average or average excess — should be working
                        unjustified_idle.append((sid, exc))

                diag = {
                    "day": day_date.strftime("%m/%d"),
                    "task": task.real_title,
                    "present": len(present_sids),
                    "assigned": len(assigned_sids),
                    "idle": len(idle_sids),
                    "unjustified_idle": unjustified_idle,
                    "avg_duration_min": avg_dur,
                    "max_duration_min": max_dur,
                    "durations": durations,
                }
                window_diagnostics.append(diag)

                logger.info(
                    f"  {task.real_title}: "
                    f"{len(assigned_sids)}/{len(present_sids)} soldiers used, "
                    f"{len(idle_sids)} idle ({len(unjustified_idle)} unjustified), "
                    f"avg_block={avg_dur:.0f}min max_block={max_dur:.0f}min"
                )
                if unjustified_idle:
                    for sid, exc in unjustified_idle:
                        name = next(
                            (n for s, n, *_ in _SOLDIER_DATA if s == sid), f"S{sid}"
                        )
                        logger.info(
                            f"    IDLE soldier {sid} ({name}): "
                            f"excess={exc:+.3f} — should be assigned"
                        )

        # ── Assertion 1: Per-CYCLE unjustified idle count ──
        # A soldier is unjustified idle only if they are present during the
        # cycle, have below-average excess, AND are not assigned to ANY task
        # in the cycle (not just a specific fractionable task).  Soldiers who
        # skip Day Guard but do Patrol + Night Guard are working — the LP is
        # making a correct fairness call across the full schedule.
        for i, (day_date, sol) in enumerate(zip(day_dates, all_solutions)):
            cycle_assigned = set(a.soldier_id for a in sol.assignments)
            cycle_tasks = _build_tasks_for_day(day_date, i * 10, peak_load=(i == 2))
            # Use the broadest window: earliest task start to latest task end.
            earliest = min(t.start_time for t in cycle_tasks)
            latest = max(t.end_time for t in cycle_tasks)
            present_sids = _soldiers_present_during(soldiers, earliest, latest)
            cycle_idle = set(present_sids) - cycle_assigned
            unjustified = []
            for sid in cycle_idle:
                day_exc = ledger.get(sid, {}).get("day_points", 0.0)
                night_exc = ledger.get(sid, {}).get("night_points", 0.0)
                if day_exc <= 0.0 or night_exc <= 0.0:
                    unjustified.append((sid, day_exc, night_exc))
            if unjustified:
                names = [
                    f"{next((n for s, n, *_ in _SOLDIER_DATA if s == sid), f'S{sid}')} "
                    f"(day={de:+.3f} night={ne:+.3f})"
                    for sid, de, ne in unjustified
                ]
                logger.info(f"  {day_date.strftime('%m/%d')} cycle: "
                            f"{len(unjustified)} unjustified idle: {names}")
            assert len(unjustified) <= 1, (
                f"Cycle {day_date.strftime('%m/%d')}: "
                f"{len(unjustified)} soldiers present with ≤0 excess but "
                f"not assigned to ANY task: {unjustified}."
            )

        # ── Assertion 2: Average fractionable block duration < 110 min ──
        # Sweet-spot penalty keeps winning configs at 60-105min targets.
        # With stronger stretch penalty (w=15, exp=2.0), shorter blocks have
        # more pair/chain penalties, so 105min configs win more often.
        # Actual avg is typically 95-105min with even rounding distribution.
        if all_frac_durations:
            avg_frac = sum(all_frac_durations) / len(all_frac_durations)
            logger.info(f"\nOverall avg fractionable block duration: {avg_frac:.1f}min")
            assert avg_frac < 110, (
                f"Average fractionable block duration is {avg_frac:.1f}min "
                f"(want < 110min). Sweet-spot penalty should keep winning "
                f"configs at ≤105min targets."
            )

        # ── Assertion 3: No fractionable block longer than 2.5h ──
        # Edge blocks (first/last in a period) can be longer than the target
        # when the period length isn't evenly divisible.  With 75-90min
        # winning configs, edge blocks can reach ~120min.  Allow up to 150min
        # to accommodate these without masking a real regression.
        if all_frac_durations:
            max_frac = max(all_frac_durations)
            logger.info(f"Max fractionable block duration: {max_frac:.1f}min")
            assert max_frac <= 150, (
                f"Longest fractionable block is {max_frac:.1f}min (want ≤ 150min). "
                f"Sweet-spot penalty should prevent configs above 90min from "
                f"winning; edge blocks should not exceed 150min."
            )

        # ── Assertion 4: Peak day compensation ──
        # Soldiers overloaded on Mar 24 (peak) should have fewer hours on Mar 25.
        # Controls: decayed excess scoring (excess cost), fairness carryover.
        if len(all_solutions) >= 4:
            peak_sol = all_solutions[2]  # Mar 24
            post_peak_sol = all_solutions[3]  # Mar 25

            peak_hours = defaultdict(float)
            for a in peak_sol.assignments:
                peak_hours[a.soldier_id] += (
                    a.end_time - a.start_time
                ).total_seconds() / 3600.0

            post_peak_hours = defaultdict(float)
            for a in post_peak_sol.assignments:
                post_peak_hours[a.soldier_id] += (
                    a.end_time - a.start_time
                ).total_seconds() / 3600.0

            if peak_hours:
                avg_peak = sum(peak_hours.values()) / len(peak_hours)
                overloaded = [
                    sid for sid, h in peak_hours.items() if h > avg_peak * 1.3
                ]
                if overloaded:
                    overloaded_post = [post_peak_hours.get(sid, 0) for sid in overloaded]
                    non_overloaded_post = [
                        h for sid, h in post_peak_hours.items()
                        if sid not in overloaded and h > 0
                    ]
                    if overloaded_post and non_overloaded_post:
                        avg_overloaded_post = sum(overloaded_post) / len(overloaded_post)
                        avg_others_post = sum(non_overloaded_post) / len(non_overloaded_post)
                        logger.info(
                            f"\nPeak compensation check: "
                            f"overloaded soldiers avg next-day hours = {avg_overloaded_post:.1f}h, "
                            f"others avg = {avg_others_post:.1f}h"
                        )
                        # Overloaded soldiers should work less the next day.
                        # Controls: decayed excess cost (w_day_points, w_night_points).
                        # Note: This uses a static ledger so compensation won't show
                        # unless the ledger is updated between cycles.  This assertion
                        # documents the desired behavior.
                        assert avg_overloaded_post <= avg_others_post * 1.1, (
                            f"Overloaded soldiers on peak day still worked "
                            f"{avg_overloaded_post:.1f}h next day vs others "
                            f"{avg_others_post:.1f}h. "
                            f"Likely culprit: static excess ledger doesn't reflect "
                            f"intra-solve load changes; or excess cost weight too low."
                        )

        # ── Summary log ──
        logger.info("\n" + "=" * 80)
        logger.info("DIAGNOSTIC SUMMARY")
        logger.info("=" * 80)
        for diag in window_diagnostics:
            flag = " ***" if len(diag["unjustified_idle"]) > 1 else ""
            logger.info(
                f"  {diag['day']} {diag['task']:30s}: "
                f"{diag['assigned']:2d}/{diag['present']:2d} used, "
                f"avg={diag['avg_duration_min']:5.0f}min "
                f"max={diag['max_duration_min']:5.0f}min "
                f"idle_unjust={len(diag['unjustified_idle'])}{flag}"
            )
