"""
seed_data.py — Realistic deployment simulation for KavManager testing.

Run:  python -m src.utils.seed_data

Creates:
  - 20 soldiers on staggered 10-on/4-off rotation (~14 present daily)
  - Daily tasks (day guard, night guard, patrol, kitchen) from Feb 1 to tomorrow
  - Simulated fair assignments for all past shifts (round-robin by lowest points)
  - Today/tomorrow tasks left for the live allocator
  - Team and individual gear
"""
import os
import sys
import random
from datetime import datetime, timedelta, time, date
from collections import defaultdict

from src.core.paths import get_project_root

PROJECT_ROOT = get_project_root()
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.core.database import SessionLocal, init_db
from src.core.models import (
    Soldier, PresenceInterval, DraftInterval, Task, TaskAssignment,
    GearItem, TeamGearItem, SoldierRequest,
)

random.seed(42)

# ── Constants ─────────────────────────────────────────────────────────────── #

DEPLOYMENT_START = date(2026, 2, 1)
DEPLOYMENT_END = date(2026, 3, 15)
TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)
NOW = datetime.now()

CYCLE_ON = 10
CYCLE_OFF = 4
CYCLE_LEN = CYCLE_ON + CYCLE_OFF  # 14-day rotation

_TOTAL_DEPLOY_DAYS = (DEPLOYMENT_END - DEPLOYMENT_START).days


def _dt(d: date, hour: int = 0, minute: int = 0) -> datetime:
    return datetime.combine(d, time(hour, minute))


def _wipe(db):
    for model in [GearItem, TeamGearItem, SoldierRequest, TaskAssignment,
                  PresenceInterval, Task, Soldier]:
        db.query(model).delete()
    db.commit()


def _is_night(dt_val: datetime) -> bool:
    """Matches the engine's night window: hour >= 23 or hour < 7."""
    return dt_val.hour >= 23 or dt_val.hour < 7


# ── Soldier definitions ───────────────────────────────────────────────────── #

SOLDIERS = [
    {
        "name": "Lior Amar",
        "roles": ["Officer", "Squad Commander"],
        "phone": "+972-50-111-0001",
        "gear": [
            {"item_name": "Binoculars", "quantity": 1, "serial_number": "BN-0051"},
            {"item_name": "Commander's Map Case", "quantity": 1, "serial_number": None},
        ],
    },
    {
        "name": "Yoni Ben-David",
        "roles": ["Sargent", "Squad Commander", "Negevist"],
        "phone": "+972-50-111-0002",
    },
    {
        "name": "Eitan Cohen",
        "roles": ["Medic", "Driver"],
        "phone": "+972-50-111-0003",
        "gear": [
            {"item_name": "Medical Bag (Combat)", "quantity": 1, "serial_number": "MD-0213"},
            {"item_name": "Tourniquet (CAT)", "quantity": 4, "serial_number": None},
        ],
    },
    {
        "name": "Avi Dagan",
        "roles": ["Operational Driver", "Driver"],
        "phone": "+972-50-111-0004",
        "gear": [
            {"item_name": "Vehicle Keys (HMMWV #1)", "quantity": 1, "serial_number": None},
        ],
    },
    {
        "name": "Shahar Eliyahu",
        "roles": ["Observer", "Navigator", "SmartShooter"],
        "phone": "+972-50-111-0005",
    },
    {
        "name": "Nimrod Friedman",
        "roles": ["Regular Drone Operator", "Observer"],
        "phone": "+972-50-111-0006",
        "gear": [
            {"item_name": "Drone Controller", "quantity": 1, "serial_number": "DC-9901"},
            {"item_name": "Spare Batteries (LiPo)", "quantity": 4, "serial_number": None},
        ],
    },
    {
        "name": "Gal Gabay",
        "roles": ["Negevist"],
        "phone": "+972-50-111-0007",
    },
    {
        "name": "Roee Harel",
        "roles": ["Matolist"],
        "phone": "+972-50-111-0008",
    },
    {
        "name": "Itai Israeli",
        "roles": ["Kala"],
        "phone": "+972-50-111-0009",
    },
    {
        "name": "Yair Jacobson",
        "roles": ["Kala", "Negevist"],
        "phone": "+972-50-111-0010",
    },
    {
        "name": "Dan Katz",
        "roles": ["Squad Commander", "Driver"],
        "phone": "+972-50-111-0011",
    },
    {
        "name": "Omer Levy",
        "roles": ["Medic"],
        "phone": "+972-50-111-0012",
        "gear": [
            {"item_name": "Medical Bag (Combat)", "quantity": 1, "serial_number": "MD-0214"},
            {"item_name": "Tourniquet (CAT)", "quantity": 4, "serial_number": None},
        ],
    },
    {
        "name": "Noam Mizrahi",
        "roles": ["Observer", "SmartShooter"],
        "phone": "+972-50-111-0013",
    },
    {
        "name": "Alon Navon",
        "roles": ["Negevist", "Driver"],
        "phone": "+972-50-111-0014",
    },
    {
        "name": "Tal Oz",
        "roles": ["Sargent"],
        "phone": "+972-50-111-0015",
    },
    {
        "name": "Roy Peretz",
        "roles": ["Operational Driver", "Driver"],
        "phone": "+972-50-111-0016",
        "gear": [
            {"item_name": "Vehicle Keys (HMMWV #2)", "quantity": 1, "serial_number": None},
        ],
    },
    {
        "name": "Ben Reshef",
        "roles": ["Regular Drone Operator"],
        "phone": "+972-50-111-0017",
    },
    {
        "name": "Gil Sasson",
        "roles": ["Navigator", "Observer"],
        "phone": "+972-50-111-0018",
    },
    {
        "name": "Erez Tamir",
        "roles": ["Explosives", "Negevist"],
        "phone": "+972-50-111-0019",
    },
    {
        "name": "Dor Yosef",
        "roles": ["Mashak-Gil", "Matolist"],
        "phone": "+972-50-111-0020",
    },
]

NUM_SOLDIERS = len(SOLDIERS)

# Per-soldier activation window: (first_day_offset, last_day_offset).
# Most cover the full deployment; a few arrive late or leave early.
ACTIVATIONS = {i: (0, _TOTAL_DEPLOY_DAYS) for i in range(NUM_SOLDIERS)}
ACTIVATIONS[17] = (6, _TOTAL_DEPLOY_DAYS)   # ROMEO arrives Feb 7
ACTIVATIONS[18] = (9, _TOTAL_DEPLOY_DAYS)   # SIERRA arrives Feb 10
ACTIVATIONS[19] = (0, 19)                   # TANGO departs Feb 20


# ── Presence logic ────────────────────────────────────────────────────────── #

def _cycle_offset(soldier_idx: int) -> int:
    """Stagger leave across the unit so ~14 soldiers are present daily."""
    return (soldier_idx * CYCLE_LEN) // NUM_SOLDIERS


def _soldier_present(soldier_idx: int, day_offset: int) -> bool:
    first, last = ACTIVATIONS[soldier_idx]
    if day_offset < first or day_offset > last:
        return False
    adjusted = day_offset - first
    cycle_pos = (adjusted + _cycle_offset(soldier_idx)) % CYCLE_LEN
    return cycle_pos < CYCLE_ON


def _generate_presence_intervals(soldier_idx: int):
    """Produce (status, start_dt, end_dt) tuples for a soldier's full deployment."""
    first, last = ACTIVATIONS[soldier_idx]
    intervals = []

    if first > 0:
        intervals.append(("ABSENT",
                          _dt(DEPLOYMENT_START, 6),
                          _dt(DEPLOYMENT_START + timedelta(days=first), 6)))

    current = _soldier_present(soldier_idx, first)
    block_start = first

    for d in range(first + 1, last + 1):
        status = _soldier_present(soldier_idx, d)
        if status != current:
            s_date = DEPLOYMENT_START + timedelta(days=block_start)
            e_date = DEPLOYMENT_START + timedelta(days=d)
            intervals.append(("PRESENT" if current else "ABSENT",
                              _dt(s_date, 6), _dt(e_date, 6)))
            block_start = d
            current = status

    s_date = DEPLOYMENT_START + timedelta(days=block_start)
    e_date = DEPLOYMENT_START + timedelta(days=min(last + 1, _TOTAL_DEPLOY_DAYS + 1))
    intervals.append(("PRESENT" if current else "ABSENT",
                      _dt(s_date, 6), _dt(e_date, 6)))

    if last < _TOTAL_DEPLOY_DAYS:
        intervals.append(("ABSENT",
                          _dt(DEPLOYMENT_START + timedelta(days=last + 1), 6),
                          _dt(DEPLOYMENT_END + timedelta(days=1), 6)))

    return intervals


# ── Task templates ────────────────────────────────────────────────────────── #

def _tasks_for_day(day: date, day_num: int):
    """Generate task definitions for a single calendar day."""
    tasks = []

    # Day Guard 07:00–19:00, 2 soldiers, fractionable
    tasks.append({
        "real_title":          f"Day Guard — {day.strftime('%d %b')}",
        "start_time":          _dt(day, 7),
        "end_time":            _dt(day, 19),
        "is_fractionable":     True,
        "required_count":      2,
        "required_roles_list": [],
        "base_weight":         1.0,
        "readiness_minutes":   0,
    })

    # Night Guard 23:00–07:00 next day, 2 soldiers, fractionable
    tasks.append({
        "real_title":          f"Night Guard — {day.strftime('%d %b')}",
        "start_time":          _dt(day, 23),
        "end_time":            _dt(day + timedelta(days=1), 7),
        "is_fractionable":     True,
        "required_count":      2,
        "required_roles_list": [],
        "base_weight":         1.5,
        "readiness_minutes":   0,
    })

    # Morning Patrol 07:30–11:30, 3 soldiers, non-fractionable
    tasks.append({
        "real_title":          f"Patrol — {day.strftime('%d %b')}",
        "start_time":          _dt(day, 7, 30),
        "end_time":            _dt(day, 11, 30),
        "is_fractionable":     False,
        "required_count":      3,
        "required_roles_list": [],
        "base_weight":         1.0,
        "readiness_minutes":   30,
    })

    # Kitchen Duty 08:00–12:00, 2 soldiers, fractionable — every other day
    if day_num % 2 == 0:
        tasks.append({
            "real_title":          f"Kitchen Duty — {day.strftime('%d %b')}",
            "start_time":          _dt(day, 8),
            "end_time":            _dt(day, 12),
            "is_fractionable":     True,
            "required_count":      2,
            "required_roles_list": [],
            "base_weight":         0.8,
            "readiness_minutes":   0,
        })

    return tasks


# ── Shift splitting (for historical assignment simulation) ────────────────── #

def _split_shifts(start: datetime, end: datetime):
    """Split a fractionable window into realistic shift blocks."""
    total_s = (end - start).total_seconds()
    night = _is_night(start)

    if night:
        chunk = timedelta(hours=2)
    elif total_s > 8 * 3600:
        chunk = timedelta(hours=4)
    else:
        return [(start, end)]

    shifts = []
    t = start
    while t < end:
        shifts.append((t, min(t + chunk, end)))
        t += chunk
    return shifts


# ── Historical assignment simulation ──────────────────────────────────────── #

def _simulate_history(db, soldiers, idx_map, all_tasks_with_data):
    """
    Create fair-distribution assignments for all shifts that ended before NOW.
    Processes shifts chronologically; picks lowest-points available soldiers.
    Returns (assignment_count, day_pts, night_pts).
    """
    day_pts = {s.id: 0.0 for s in soldiers}
    night_pts = {s.id: 0.0 for s in soldiers}
    busy_until = {s.id: datetime.min for s in soldiers}

    all_shifts = []
    for task_obj, t_data in all_tasks_with_data:
        if t_data["is_fractionable"]:
            split = _split_shifts(t_data["start_time"], t_data["end_time"])
        else:
            split = [(t_data["start_time"], t_data["end_time"])]

        needed = t_data["required_count"]
        for s_start, s_end in split:
            if s_end <= NOW:
                all_shifts.append((task_obj, s_start, s_end, needed))

    all_shifts.sort(key=lambda x: x[1])

    count = 0
    underfilled = 0

    for task_obj, s_start, s_end, needed in all_shifts:
        night = _is_night(s_start)

        check_date = s_start.date()
        if s_start.hour < 7:
            check_date -= timedelta(days=1)
        day_off = (check_date - DEPLOYMENT_START).days
        if day_off < 0:
            continue

        pts = night_pts if night else day_pts

        available = []
        for s in soldiers:
            if not _soldier_present(idx_map[s.id], day_off):
                continue
            if busy_until[s.id] > s_start:
                continue
            gap_h = (s_start - busy_until[s.id]).total_seconds() / 3600
            penalty = 10.0 if 0 < gap_h < 2 else 0.0
            available.append((s, pts[s.id] + penalty + random.uniform(0, 0.3)))

        available.sort(key=lambda x: x[1])
        picked = [s for s, _ in available[:needed]]

        if len(picked) < needed:
            underfilled += 1

        weight = (task_obj.base_weight or 1.0) * (s_end - s_start).total_seconds() / 3600

        for s in picked:
            db.add(TaskAssignment(
                task_id=task_obj.id,
                soldier_id=s.id,
                start_time=s_start,
                end_time=s_end,
                final_weight_applied=weight,
            ))
            if night:
                night_pts[s.id] += weight
            else:
                day_pts[s.id] += weight
            busy_until[s.id] = s_end
            count += 1

    db.flush()

    # Resync the per-present-day rate metric into soldier columns.
    from src.utils.maintenance import resync_soldier_rates
    db.flush()
    resync_soldier_rates(db)
    db.flush()

    if underfilled:
        print(f"   ⚠  {underfilled} shift(s) could not be fully staffed (not enough available soldiers).")

    return count, day_pts, night_pts


# ── Gear ──────────────────────────────────────────────────────────────────── #

TEAM_GEAR = [
    {"item_name": "M16 Rifle",               "quantity": 20, "serial_number": None,      "notes": "All assigned to soldiers"},
    {"item_name": "Body Armor (Level IV)",    "quantity": 20, "serial_number": None,      "notes": "Check webbing before deployment"},
    {"item_name": "Helmet (Ballistic)",       "quantity": 20, "serial_number": None,      "notes": None},
    {"item_name": "Radio Set (PRC-710)",      "quantity": 6,  "serial_number": "RC-0041", "notes": "Battery charging schedule in binder"},
    {"item_name": "Night Vision Monocular",   "quantity": 4,  "serial_number": "NV-7712", "notes": "Handle with care"},
    {"item_name": "First Aid Kit (IFAK)",     "quantity": 24, "serial_number": None,      "notes": "Restock after any use"},
    {"item_name": "Stretcher (Folding)",      "quantity": 3,  "serial_number": None,      "notes": None},
    {"item_name": "Vehicle (HMMWV)",          "quantity": 3,  "serial_number": "VH-3310", "notes": "Maintenance every 500km"},
    {"item_name": "Drone (Matrice 300)",      "quantity": 1,  "serial_number": "DR-9901", "notes": "Pre-flight checklist required"},
    {"item_name": "Negev LMG",               "quantity": 4,  "serial_number": "NG-2201", "notes": "Barrel swap kit in armoury"},
    {"item_name": "MAG GPMG",                "quantity": 2,  "serial_number": "MG-1150", "notes": None},
    {"item_name": "Anti-Tank Missile (Gil)",  "quantity": 2,  "serial_number": "AT-5501", "notes": "Storage temp < 40°C"},
]

# ── Main seed ─────────────────────────────────────────────────────────────── #

def seed():
    print("\n=== KavManager — Realistic Deployment Seed ===\n")

    print("1. Initialising database…")
    init_db()

    db = SessionLocal()
    try:
        print("2. Wiping existing data…")
        _wipe(db)

        # ── Soldiers ─────────────────────────────────────────────── #
        print(f"3. Creating {NUM_SOLDIERS} soldiers…")
        soldiers = []
        idx_map = {}

        for i, s_data in enumerate(SOLDIERS):
            s = Soldier(
                name=s_data["name"],
                phone_number=s_data["phone"],
                role=s_data["roles"],
                is_active_in_kav=True,
                total_day_points=0.0,
                total_night_points=0.0,
                active_reserve_days=0,
                present_days_count=0.0,
            )
            db.add(s)
            soldiers.append(s)

        db.flush()
        for i, s in enumerate(soldiers):
            idx_map[s.id] = i

        # ── Presence intervals ───────────────────────────────────── #
        print("4. Creating presence + draft intervals…")
        iv_count = 0
        for i in range(NUM_SOLDIERS):
            for status, start, end in _generate_presence_intervals(i):
                db.add(PresenceInterval(
                    soldier_id=soldiers[i].id,
                    start_time=start, end_time=end, status=status,
                ))
                iv_count += 1
            # Drafted window = from first activation day until last activation day
            first, last = ACTIVATIONS[i]
            draft_start = _dt(DEPLOYMENT_START + timedelta(days=first), 0, 0)
            draft_end = _dt(DEPLOYMENT_START + timedelta(days=last + 1), 0, 0)
            db.add(DraftInterval(
                soldier_id=soldiers[i].id,
                start_time=draft_start,
                end_time=draft_end,
                status="DRAFTED",
            ))
        db.flush()
        print(f"   {iv_count} presence intervals and {NUM_SOLDIERS} draft intervals.")

        days_to_today = (TODAY - DEPLOYMENT_START).days
        for i, s in enumerate(soldiers):
            present = sum(1 for d in range(days_to_today + 1)
                          if _soldier_present(i, d))
            s.present_days_count = float(present)
            first, last = ACTIVATIONS[i]
            s.active_reserve_days = min(days_to_today, last) - first
        db.flush()

        # ── Tasks ────────────────────────────────────────────────── #
        print("5. Creating tasks…")
        all_tasks_with_data = []
        task_count = 0

        day = DEPLOYMENT_START
        day_num = 1
        while day <= TOMORROW and day <= DEPLOYMENT_END:
            for t_data in _tasks_for_day(day, day_num):
                task = Task(
                    real_title=t_data["real_title"],
                    start_time=t_data["start_time"],
                    end_time=t_data["end_time"],
                    is_fractionable=t_data["is_fractionable"],
                    required_count=t_data["required_count"],
                    required_roles_list=t_data["required_roles_list"],
                    base_weight=t_data["base_weight"],
                    readiness_minutes=t_data["readiness_minutes"],
                    is_active=True,
                    coverage_status="OK",
                )
                db.add(task)
                db.flush()
                all_tasks_with_data.append((task, t_data))
                task_count += 1

            day += timedelta(days=1)
            day_num += 1

        past_count = sum(1 for _, d in all_tasks_with_data if d["end_time"] <= NOW)
        print(f"   {task_count} tasks ({past_count} fully past, "
              f"{task_count - past_count} active/future).")

        # ── Historical assignments ───────────────────────────────── #
        print("6. Simulating historical assignments (fair round-robin)…")
        asgn_count, day_pts, night_pts = _simulate_history(
            db, soldiers, idx_map, all_tasks_with_data
        )
        print(f"   {asgn_count} assignments created.")

        # ── Gear ─────────────────────────────────────────────────── #
        print(f"7. Adding {len(TEAM_GEAR)} team gear items…")
        for g in TEAM_GEAR:
            db.add(TeamGearItem(
                item_name=g["item_name"], quantity=g["quantity"],
                serial_number=g["serial_number"], notes=g["notes"],
            ))

        ind_count = 0
        print("8. Adding individual gear items…")
        for soldier, meta in zip(soldiers, SOLDIERS):
            for g in meta.get("gear", []):
                db.add(GearItem(
                    soldier_id=soldier.id,
                    item_name=g["item_name"],
                    quantity=g["quantity"],
                    serial_number=g["serial_number"],
                ))
                ind_count += 1

        db.commit()
        print("   ✓ All data committed.\n")

        # ── Reconcile current/future tasks ───────────────────────── #
        print("9. Reconciling current/future tasks…")
        try:
            from src.services.schedule_service import ScheduleService
            ScheduleService(db).reconcile()
            print("   ✓ Reconcile complete.")
        except Exception as exc:
            print(f"   ⚠  Reconcile error: {exc}")
            print("      Start the app and reconcile manually.")

        # ── Summary ──────────────────────────────────────────────── #
        print("\n" + "=" * 55)
        print("  SEED COMPLETE")
        print("=" * 55)
        print(f"  Soldiers         : {NUM_SOLDIERS}")
        print(f"  Presence ints    : {iv_count}")
        print(f"  Tasks            : {task_count}")
        print(f"  Past assignments : {asgn_count}")
        print(f"  Team gear        : {len(TEAM_GEAR)}")
        print(f"  Individual gear  : {ind_count}")
        print()

        id_to_label = {s.id: (s.name or f"#{s.id}") for s in soldiers}
        sorted_day = sorted(day_pts.items(), key=lambda x: x[1], reverse=True)
        sorted_night = sorted(night_pts.items(), key=lambda x: x[1], reverse=True)

        print("  Day points (top 5 / bottom 5):")
        for sid, pts in sorted_day[:5]:
            print(f"    {id_to_label[sid]:10s} {pts:7.1f}")
        print("    ...")
        for sid, pts in sorted_day[-5:]:
            print(f"    {id_to_label[sid]:10s} {pts:7.1f}")

        print("  Night points (top 5 / bottom 5):")
        for sid, pts in sorted_night[:5]:
            print(f"    {id_to_label[sid]:10s} {pts:7.1f}")
        print("    ...")
        for sid, pts in sorted_night[-5:]:
            print(f"    {id_to_label[sid]:10s} {pts:7.1f}")

        today_off = (TODAY - DEPLOYMENT_START).days
        present_today = [s for i, s in enumerate(soldiers)
                         if _soldier_present(i, today_off)]
        print(f"\n  Present today: {len(present_today)} soldiers")
        print(f"    {', '.join((s.name or f'#{s.id}') for s in present_today)}")

        print(f"\nLaunch:  QT_QPA_PLATFORM=xcb python main.py\n")

    finally:
        db.close()


if __name__ == "__main__":
    seed()
