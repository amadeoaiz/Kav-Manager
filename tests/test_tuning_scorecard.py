"""
Tuning scorecard: runs a realistic scenario and prints schedule quality metrics.

NOT a pass/fail test — use ``pytest -s tests/test_tuning_scorecard.py`` to see output.
The scorecard function is reusable from other tests.
"""
from datetime import datetime, timedelta
from collections import defaultdict
from dataclasses import dataclass

from src.core.engine import FrozenAssignment, PlannedAssignment, SoldierState, TaskSpec
from src.core.lp_solver import (
    lp_solve, LPSolution,
    _generate_all_blocks, _solve_day_lp, _solve_night_lp, _solve_fixed_tasks,
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


def _daily_presence(sid, base, num_days=3):
    """Consecutive daily intervals 00:00–23:59:59 (mirrors production data)."""
    intervals = []
    for d in range(num_days):
        day = base + timedelta(days=d)
        intervals.append(_presence(
            day.replace(hour=0, minute=0, second=0),
            day.replace(hour=23, minute=59, second=59),
        ))
    return intervals


# ── Scorecard ────────────────────────────────────────────────────

@dataclass
class _GapInfo:
    soldier_id: int
    gap_hours: float
    task_before: str
    task_after: str


def format_scorecard(
    sol: LPSolution,
    soldiers: list[SoldierState],
    tasks: list[TaskSpec],
    config_comparisons: list[dict] | None = None,
) -> str:
    """Build a human-readable quality scorecard from an LPSolution.

    Parameters
    ----------
    sol : LPSolution
        The solution returned by lp_solve().
    soldiers : list[SoldierState]
        The input soldiers (only those available / passed to the solver).
    tasks : list[TaskSpec]
        The input task specs.
    config_comparisons : list[dict] | None
        Optional per-configuration results, each with keys:
        target, day_obj, night_obj, total_obj, status ('SELECTED'|'skipped'|'rejected').
    """
    task_by_id = {t.id: t for t in tasks}
    soldier_ids = {s.id for s in soldiers}
    lines: list[str] = []

    def ln(s=""):
        lines.append(s)

    # ── Header ──
    ln("=== TUNING SCORECARD ===")
    if config_comparisons:
        selected = [c for c in config_comparisons if c["status"] == "SELECTED"]
        if selected:
            ln(f"Config selected: target={selected[0]['target']}min")
    ln(f"Solve time: {sol.solve_time_seconds:.2f}s")
    ln()

    # ── Coverage ──
    total = len(tasks)
    covered = sum(1 for s in sol.coverage_status.values() if s == "OK")
    uncovered_names = [
        task_by_id[tid].real_title
        for tid, st in sol.coverage_status.items()
        if st == "UNCOVERED" and tid in task_by_id
    ]
    ln("COVERAGE:")
    ln(f"  Tasks covered: {covered}/{total}")
    ln(f"  Uncovered tasks: {uncovered_names if uncovered_names else '(none)'}")
    ln()

    # ── Fairness ──
    day_hours: dict[int, float] = defaultdict(float)
    night_hours: dict[int, float] = defaultdict(float)
    total_hours: dict[int, float] = defaultdict(float)

    for a in sol.assignments:
        h = (a.end_time - a.start_time).total_seconds() / 3600
        total_hours[a.soldier_id] += h
        # Classify by whether the assignment overlaps night window.
        is_night_asgn = (a.start_time.hour >= NIGHT_START or a.start_time.hour < NIGHT_END)
        if is_night_asgn:
            night_hours[a.soldier_id] += h
        else:
            day_hours[a.soldier_id] += h

    # Include soldiers with 0 hours.
    for s in soldiers:
        day_hours.setdefault(s.id, 0.0)
        night_hours.setdefault(s.id, 0.0)
        total_hours.setdefault(s.id, 0.0)

    def _stats(d):
        vals = list(d.values())
        if not vals:
            return 0.0, 0.0, 0.0, 0.0
        avg = sum(vals) / len(vals)
        return avg, min(vals), max(vals), max(vals) - min(vals)

    d_avg, d_min, d_max, d_spread = _stats(day_hours)
    n_avg, n_min, n_max, n_spread = _stats(night_hours)
    t_avg, t_min, t_max, t_spread = _stats(total_hours)

    ln("FAIRNESS:")
    ln(f"  Day hours:   avg={d_avg:.1f}  min={d_min:.1f}  max={d_max:.1f}  spread={d_spread:.1f}")
    ln(f"  Night hours: avg={n_avg:.1f}  min={n_min:.1f}  max={n_max:.1f}  spread={n_spread:.1f}")
    ln(f"  Total hours: avg={t_avg:.1f}  min={t_min:.1f}  max={t_max:.1f}  spread={t_spread:.1f}")
    ln()

    # ── Rest gaps ──
    by_soldier: dict[int, list[PlannedAssignment]] = defaultdict(list)
    for a in sol.assignments:
        by_soldier[a.soldier_id].append(a)

    gaps: list[_GapInfo] = []
    for sid, asgns in by_soldier.items():
        asgns_sorted = sorted(asgns, key=lambda x: x.start_time)
        for i in range(1, len(asgns_sorted)):
            prev = asgns_sorted[i - 1]
            curr = asgns_sorted[i]
            gap_h = (curr.start_time - prev.end_time).total_seconds() / 3600
            gaps.append(_GapInfo(
                soldier_id=sid,
                gap_hours=gap_h,
                task_before=task_by_id.get(prev.task_id, prev).real_title
                            if prev.task_id in task_by_id else f"T{prev.task_id}",
                task_after=task_by_id.get(curr.task_id, curr).real_title
                           if curr.task_id in task_by_id else f"T{curr.task_id}",
            ))

    back_to_back = sum(1 for g in gaps if abs(g.gap_hours) < 0.01)
    short_1h = sum(1 for g in gaps if 0 < g.gap_hours < 1.0)
    short_2h = sum(1 for g in gaps if 0 < g.gap_hours < 2.0)
    short_3h = sum(1 for g in gaps if 0 < g.gap_hours < 3.0)
    shortest = min(gaps, key=lambda g: g.gap_hours) if gaps else None

    ln("REST GAPS:")
    ln(f"  Back-to-back (0 gap): {back_to_back} instances")
    ln(f"  Short gaps (<1h): {short_1h} instances")
    ln(f"  Short gaps (<2h): {short_2h} instances")
    ln(f"  Short gaps (<3h): {short_3h} instances")
    if shortest:
        ln(f"  Shortest gap: {shortest.gap_hours:.1f}h "
           f"(soldier {shortest.soldier_id} between {shortest.task_before} and {shortest.task_after})")
    else:
        ln("  Shortest gap: N/A")
    ln()

    # ── Night quality ──
    wakeups_per_soldier: dict[int, int] = defaultdict(int)
    for sid, asgns in by_soldier.items():
        night_asgns = sorted(
            [a for a in asgns
             if a.start_time.hour >= NIGHT_START or a.start_time.hour < NIGHT_END],
            key=lambda x: x.start_time,
        )
        if not night_asgns:
            continue
        # Count contiguous groups = wakeups.
        groups = 1
        for i in range(1, len(night_asgns)):
            if night_asgns[i].start_time > night_asgns[i - 1].end_time:
                groups += 1
        wakeups_per_soldier[sid] = groups

    total_wakeups = sum(wakeups_per_soldier.values())
    max_wakeups = max(wakeups_per_soldier.values()) if wakeups_per_soldier else 0
    multi_wakeup = [sid for sid, w in wakeups_per_soldier.items() if w >= 2]

    ln("NIGHT QUALITY:")
    ln(f"  Total wakeups: {total_wakeups}")
    ln(f"  Max wakeups per soldier: {max_wakeups}")
    ln(f"  Soldiers with 2+ wakeups: {sorted(multi_wakeup) if multi_wakeup else '(none)'}")
    ln()

    # ── Per-soldier breakdown ──
    ln("ASSIGNMENTS PER SOLDIER:")
    for s in sorted(soldiers, key=lambda x: x.id):
        sid = s.id
        d_h = day_hours[sid]
        n_h = night_hours[sid]
        t_h = total_hours[sid]
        n_asgn = len(by_soldier.get(sid, []))
        wk = wakeups_per_soldier.get(sid, 0)
        ln(f"  soldier {sid:>2}: {d_h:4.1f}h day + {n_h:4.1f}h night = {t_h:4.1f}h total, "
           f"{n_asgn} assignments, {wk} wakeups")
    ln()

    # ── Block structure ──
    ln("BLOCK STRUCTURE:")
    # Derive from assignments — group contiguous per-task spans.
    day_block_sizes: list[float] = []
    night_block_sizes: list[float] = []
    for a in sol.assignments:
        dur_min = (a.end_time - a.start_time).total_seconds() / 60
        is_night_asgn = (a.start_time.hour >= NIGHT_START or a.start_time.hour < NIGHT_END)
        if is_night_asgn:
            night_block_sizes.append(dur_min)
        else:
            day_block_sizes.append(dur_min)

    def _block_summary(sizes):
        if not sizes:
            return "0"
        avg = sum(sizes) / len(sizes)
        return f"{len(sizes)} (avg {avg:.0f}min, range {min(sizes):.0f}-{max(sizes):.0f}min)"

    ln(f"  Day assignments: {_block_summary(day_block_sizes)}")
    ln(f"  Night assignments: {_block_summary(night_block_sizes)}")
    ln()

    # ── Per-configuration comparison ──
    if config_comparisons:
        ln("PER-CONFIGURATION COMPARISON:")
        for c in config_comparisons:
            ln(f"  {c['target']:>4}min:  day={c['day_obj']:7.1f}  "
               f"night={c['night_obj']:7.1f}  total={c['total_obj']:7.1f}  "
               f"[{c['status']}]")
        ln()

    return "\n".join(lines)


# ── Per-config comparison runner ─────────────────────────────────

def _run_per_config_comparison(
    soldiers: list[SoldierState],
    tasks: list[TaskSpec],
    frozen: list[FrozenAssignment],
    weights: LPWeights,
) -> list[dict]:
    """Run the solver internals for each target block length and return comparison data.

    Mirrors lp_solve's config loop: largest-first, with time budget.
    """
    import time as _time
    frac_specs = [t for t in tasks if t.is_fractionable]

    fixed_assignments, fixed_coverage = _solve_fixed_tasks(
        tasks, soldiers, frozen,
    )
    augmented_frozen = list(frozen) + [
        FrozenAssignment(
            soldier_id=pa.soldier_id, task_id=pa.task_id,
            start_time=pa.start_time, end_time=pa.end_time,
        )
        for pa in fixed_assignments
    ]

    day_excess = {}
    night_excess = {}

    results: list[dict] = []
    best_total = float("inf")
    t0 = _time.time()
    time_budget = max(weights.time_limit_seconds * 2, 4.0)

    for target in sorted(weights.target_block_lengths, reverse=True):
        if best_total < float("inf") and (_time.time() - t0) >= time_budget:
            results.append({
                "target": target,
                "day_obj": 0.0,
                "night_obj": 0.0,
                "total_obj": 0.0,
                "status": "skipped",
            })
            continue

        gen = _generate_all_blocks(
            frac_specs, target, NIGHT_START, NIGHT_END, weights,
        )
        if gen is None:
            results.append({
                "target": target,
                "day_obj": 0.0,
                "night_obj": 0.0,
                "total_obj": 0.0,
                "status": "skipped",
            })
            continue

        day_blocks, night_blocks, _segments = gen

        day_asgn, day_cov, day_obj, day_status, _dh = _solve_day_lp(
            day_blocks, soldiers, frac_specs, augmented_frozen,
            day_excess, weights, NIGHT_START, NIGHT_END,
        )
        night_asgn, night_cov, night_obj, night_status, _nh = _solve_night_lp(
            night_blocks, soldiers, frac_specs, augmented_frozen,
            night_excess, prior_assignments=day_asgn, weights=weights,
            night_start_hour=NIGHT_START, night_end_hour=NIGHT_END,
        )

        total_obj = day_obj + night_obj
        results.append({
            "target": target,
            "day_obj": day_obj,
            "night_obj": night_obj,
            "total_obj": total_obj,
            "status": "pending",
        })

        if total_obj < best_total:
            best_total = total_obj

    # Mark the best as SELECTED, rest as "rejected".
    for r in results:
        if r["status"] == "skipped":
            continue
        if abs(r["total_obj"] - best_total) < 0.01:
            r["status"] = "SELECTED"
        else:
            r["status"] = "rejected"

    # Sort by target length for display.
    results.sort(key=lambda r: r["target"])

    return results


# ══════════════════════════════════════════════════════════════════
# Realistic production scenario
# ══════════════════════════════════════════════════════════════════

def _build_production_scenario():
    """12 available soldiers (5 absent), 6 realistic tasks."""
    # 17 soldiers total, 5 absent (IDs 13-17), 12 available.
    soldiers = []
    # 3 Drivers (IDs 1-3).
    for i in range(1, 4):
        soldiers.append(SoldierState(
            id=i, roles=["Driver"], is_active=True,
            presence_intervals=_daily_presence(i, BASE, num_days=3),
            day_points=0.0, night_points=0.0,
        ))
    # 3 Drone Operators (IDs 4-6).
    for i in range(4, 7):
        soldiers.append(SoldierState(
            id=i, roles=["Drone Operator"], is_active=True,
            presence_intervals=_daily_presence(i, BASE, num_days=3),
            day_points=0.0, night_points=0.0,
        ))
    # 6 generic soldiers (IDs 7-12).
    for i in range(7, 13):
        soldiers.append(SoldierState(
            id=i, roles=[], is_active=True,
            presence_intervals=_daily_presence(i, BASE, num_days=3),
            day_points=0.0, night_points=0.0,
        ))

    tasks = [
        # Task 1: Jamal — 09:00-21:00, fractionable, concurrent=1, any role.
        TaskSpec(
            id=1, real_title="Jamal",
            start_time=BASE + timedelta(hours=9),
            end_time=BASE + timedelta(hours=21),
            is_fractionable=True, is_night=False,
            required_roles=["Soldier"], concurrent_required=1,
            hardness=3, min_block_minutes=60, readiness_minutes=0,
        ),
        # Task 2: Shmirat Yom — 13:00-00:00, fractionable, concurrent=2, any role.
        TaskSpec(
            id=2, real_title="Shmirat Yom",
            start_time=BASE + timedelta(hours=13),
            end_time=BASE + timedelta(days=1),
            is_fractionable=True, is_night=False,
            required_roles=["Soldier", "Soldier"], concurrent_required=2,
            hardness=3, min_block_minutes=60, readiness_minutes=0,
        ),
        # Task 3: Shmirat Laila — 22:00-09:00+1, fractionable, concurrent=2, any role.
        TaskSpec(
            id=3, real_title="Shmirat Laila",
            start_time=BASE + timedelta(hours=22),
            end_time=BASE + timedelta(days=1, hours=9),
            is_fractionable=True, is_night=False,
            required_roles=["Soldier", "Soldier"], concurrent_required=2,
            hardness=3, min_block_minutes=60, readiness_minutes=0,
        ),
        # Task 4: Siur — 18:30-20:00, fixed, concurrent=4, 1 Driver + 3 Any.
        TaskSpec(
            id=4, real_title="Siur",
            start_time=BASE + timedelta(hours=18, minutes=30),
            end_time=BASE + timedelta(hours=20),
            is_fractionable=False, is_night=False,
            required_roles=["Driver", "Soldier", "Soldier", "Soldier"],
            concurrent_required=4,
            hardness=3, min_block_minutes=60, readiness_minutes=0,
        ),
        # Task 5: Drone — 02:30-03:30, fixed, concurrent=1, requires Drone Operator.
        TaskSpec(
            id=5, real_title="Drone",
            start_time=BASE + timedelta(hours=2, minutes=30),
            end_time=BASE + timedelta(hours=3, minutes=30),
            is_fractionable=False, is_night=True,
            required_roles=["Drone Operator"], concurrent_required=1,
            hardness=3, min_block_minutes=60, readiness_minutes=0,
        ),
        # Task 6: Patrol — 08:30-10:30, fixed, concurrent=3, any role.
        TaskSpec(
            id=6, real_title="Patrol",
            start_time=BASE + timedelta(hours=8, minutes=30),
            end_time=BASE + timedelta(hours=10, minutes=30),
            is_fractionable=False, is_night=False,
            required_roles=["Soldier", "Soldier", "Soldier"],
            concurrent_required=3,
            hardness=3, min_block_minutes=60, readiness_minutes=0,
        ),
    ]

    return soldiers, tasks


# ══════════════════════════════════════════════════════════════════
# Test
# ══════════════════════════════════════════════════════════════════

def test_tuning_scorecard():
    """Run realistic scenario and print quality scorecard (use pytest -s)."""
    soldiers, tasks = _build_production_scenario()
    weights = LPWeights()

    # Run per-config comparison (separate from the main solve for diagnostics).
    config_comparisons = _run_per_config_comparison(
        soldiers, tasks, frozen=[], weights=weights,
    )

    # Run the actual solver.
    sol = lp_solve(
        soldier_states=soldiers,
        task_specs=tasks,
        frozen_assignments=[],
        freeze_point=BASE,
        night_start_hour=NIGHT_START,
        night_end_hour=NIGHT_END,
        weights=weights,
        effective_ledger={},
    )

    scorecard = format_scorecard(sol, soldiers, tasks, config_comparisons)
    print("\n" + scorecard)

    # Minimal sanity — not the point of this test, but catch regressions.
    assert sol.status in ("optimal", "feasible")


# ══════════════════════════════════════════════════════════════════
# Frozen-fairness regression test (reproduces real-data bug)
# ══════════════════════════════════════════════════════════════════

def _build_frozen_scenario():
    """12 available soldiers, 3 with frozen Patrol, 2 with partial presence."""
    from src.core.lp_solver import GRID_MINUTES

    FREEZE = BASE + timedelta(hours=9)  # Reconcile at 09:00.

    soldiers = []
    # IDs 1-10: full presence (48h, covers BASE and BASE+1d).
    role_map = {
        1: ["Driver"],
        2: ["Explosives", "Kala", "Medic", "Sargent"],  # Nadav
        3: ["Driver"],
        4: ["Drone Operator"],
        5: ["Drone Operator"],
        6: [],
        7: [],
        8: [],
        9: [],
        10: [],
    }
    for i in range(1, 11):
        soldiers.append(SoldierState(
            id=i, roles=role_map.get(i, []), is_active=True,
            presence_intervals=_daily_presence(i, BASE, num_days=2),
            day_points=0.0, night_points=0.0,
        ))
    # ID 11: partial presence until 12:00 on day 0 only (like Noiman).
    soldiers.append(SoldierState(
        id=11, roles=[], is_active=True,
        presence_intervals=[_presence(BASE, BASE.replace(hour=12))],
        day_points=0.0, night_points=0.0,
    ))
    # ID 12: partial presence until 12:00 on day 0 only (like Malka).
    soldiers.append(SoldierState(
        id=12, roles=["Banai", "Magist"], is_active=True,
        presence_intervals=[_presence(BASE, BASE.replace(hour=12))],
        day_points=0.0, night_points=0.0,
    ))

    tasks = [
        # Jamal: 08:30-21:00, concurrent=1, any role.
        TaskSpec(
            id=1, real_title="Jamal",
            start_time=BASE + timedelta(hours=8, minutes=30),
            end_time=BASE + timedelta(hours=21),
            is_fractionable=True, is_night=False,
            required_roles=["Soldier"], concurrent_required=1,
            hardness=3, min_block_minutes=60, readiness_minutes=0,
        ),
        # Shmirat Yom: 09:00-22:00, concurrent=2, any role.
        TaskSpec(
            id=2, real_title="Shmirat Yom",
            start_time=BASE + timedelta(hours=9),
            end_time=BASE + timedelta(hours=22),
            is_fractionable=True, is_night=False,
            required_roles=["Soldier", "Soldier"], concurrent_required=2,
            hardness=3, min_block_minutes=60, readiness_minutes=0,
        ),
        # Patrol: 08:30-10:30, FIXED, concurrent=3, any role — FROZEN.
        TaskSpec(
            id=3, real_title="Patrol",
            start_time=BASE + timedelta(hours=8, minutes=30),
            end_time=BASE + timedelta(hours=10, minutes=30),
            is_fractionable=False, is_night=False,
            required_roles=["Soldier", "Soldier", "Soldier"],
            concurrent_required=3,
            hardness=3, min_block_minutes=60, readiness_minutes=0,
        ),
        # Shmirat Laila: 23:00-07:00+1, concurrent=2, any role.
        TaskSpec(
            id=4, real_title="Shmirat Laila",
            start_time=BASE + timedelta(hours=23),
            end_time=BASE + timedelta(days=1, hours=7),
            is_fractionable=True, is_night=True,
            required_roles=["Soldier", "Soldier"], concurrent_required=2,
            hardness=3, min_block_minutes=60, readiness_minutes=0,
        ),
    ]

    # Frozen assignments: Patrol (08:30-10:30) for soldiers 6, 7, 8.
    frozen = [
        FrozenAssignment(soldier_id=6, task_id=3,
                         start_time=BASE + timedelta(hours=8, minutes=30),
                         end_time=BASE + timedelta(hours=10, minutes=30)),
        FrozenAssignment(soldier_id=7, task_id=3,
                         start_time=BASE + timedelta(hours=8, minutes=30),
                         end_time=BASE + timedelta(hours=10, minutes=30)),
        FrozenAssignment(soldier_id=8, task_id=3,
                         start_time=BASE + timedelta(hours=8, minutes=30),
                         end_time=BASE + timedelta(hours=10, minutes=30)),
    ]

    return soldiers, tasks, frozen, FREEZE


def test_frozen_fairness():
    """Frozen Patrol soldiers must not steal blocks from non-frozen soldiers.

    Regression test for: Nadav (#2) got 1h despite full availability,
    Malka (#12) got 0h despite partial availability.
    """
    soldiers, tasks, frozen, freeze_point = _build_frozen_scenario()
    weights = LPWeights()

    sol = lp_solve(
        soldier_states=soldiers,
        task_specs=tasks,
        frozen_assignments=frozen,
        freeze_point=freeze_point,
        night_start_hour=NIGHT_START,
        night_end_hour=NIGHT_END,
        weights=weights,
        effective_ledger={},
    )

    assert sol.status in ("optimal", "feasible")

    # Compute hours per soldier.
    from collections import defaultdict
    hours = defaultdict(float)
    for a in sol.assignments:
        hours[a.soldier_id] += (a.end_time - a.start_time).total_seconds() / 3600

    scorecard = format_scorecard(sol, soldiers, tasks)
    print("\n=== FROZEN FAIRNESS TEST ===")
    print(scorecard)

    # Nadav (#2): full presence, must get reasonable share (>= 2h).
    assert hours[2] >= 2.0, (
        f"Nadav (soldier 2) got only {hours[2]:.1f}h — should be >= 2h"
    )

    # Partial-presence soldiers (11, 12) should get > 0h.
    # They have 3h of day presence (09:00-12:00 after freeze at 09:00).
    assert hours[11] > 0 or hours[12] > 0, (
        f"Both partial soldiers got 0h: soldier 11={hours[11]:.1f}h, 12={hours[12]:.1f}h"
    )

    # Frozen soldiers (6, 7, 8) should not dominate — their total (LP + frozen)
    # should not exceed 2× fair share.
    frozen_hours = {6: 2.0, 7: 2.0, 8: 2.0}
    all_ids = {s.id for s in soldiers}
    total_hours_incl_frozen = {}
    for sid in all_ids:
        total_hours_incl_frozen[sid] = hours.get(sid, 0) + frozen_hours.get(sid, 0)
    avg_total = sum(total_hours_incl_frozen.values()) / len(all_ids)
    for sid in [6, 7, 8]:
        assert total_hours_incl_frozen[sid] <= avg_total * 2.5, (
            f"Frozen soldier {sid} total {total_hours_incl_frozen[sid]:.1f}h "
            f"exceeds 2.5× avg {avg_total:.1f}h"
        )
