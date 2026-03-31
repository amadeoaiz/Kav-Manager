"""
KavManager Matrix Bot  (E2E-encrypted, menu-based state machine)

Can run standalone (python -m src.api.bot) or embedded inside the desktop
app via MatrixBotRunner, which wraps the bot in a daemon thread.

Shares data/app.db with the desktop app (WAL mode enabled in database.py).
All messages are E2E encrypted; the bot uses real names for soldier display.
"""
import asyncio
import logging
import math
import os
import sys
import threading
import time as _time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, date, time, timedelta

from nio import (
    AsyncClient,
    AsyncClientConfig,
    LoginResponse,
    MatrixRoom,
    RoomMessageText,
    InviteMemberEvent,
    RoomCreateResponse,
    RoomResolveAliasResponse,
)

from src.core.paths import get_project_root, get_data_dir

PROJECT_ROOT = get_project_root()
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.core.database import SessionLocal, init_db
from src.core.models import GearItem, Soldier, Task, TaskAssignment, TeamGearItem
from src.services.config_service import ConfigService
from src.services.gear_service import GearService
from src.services.readiness_service import get_day_readiness, get_day_schedule
from src.services.request_service import RequestService
from src.services.schedule_service import ScheduleService
from src.services.soldier_service import SoldierService
from src.services.task_service import TaskService
from src.services.template_service import TemplateService
from src.api.bot_texts import t, ordinal

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("kavbot")

NIO_STORE_PATH = os.path.join(get_data_dir(), "nio_store")
os.makedirs(NIO_STORE_PATH, exist_ok=True)


# ── Session context manager ───────────────────────────────────────────────────

@contextmanager
def bot_session():
    """Context manager for bot DB sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Per-user state ─────────────────────────────────────────────────────────────

# { matrix_user_id: { "state": str, "lang": str, "data": dict } }
_user_state: dict[str, dict] = {}


def _get_state(user_id: str) -> dict:
    if user_id not in _user_state:
        _user_state[user_id] = {"state": None, "lang": "en", "data": {}}
    return _user_state[user_id]


def _set_state(user_id: str, state: str, **data_updates):
    us = _get_state(user_id)
    us["state"] = state
    if data_updates:
        us["data"].update(data_updates)


def _go_main_menu(user_id: str):
    us = _get_state(user_id)
    us["state"] = "main_menu"
    us["data"] = {}


# ── Display helpers ────────────────────────────────────────────────────────────

def display_name(s: Soldier) -> str:
    return s.name or f"#{s.id}"


def task_display(tk: Task) -> str:
    return tk.real_title or f"Task#{tk.id}"


def _is_night_hour(hour: int, night_start: int, night_end: int) -> bool:
    return hour >= night_start or hour < night_end


def _is_privileged(soldier: Soldier, db) -> bool:
    """Check if soldier has privileged access (command chain member OR Sargent role)."""
    config = ConfigService(db).get_config()
    if not config:
        return False
    chain = config.command_chain or []
    if soldier.id in chain:
        return True
    roles = soldier.role or []
    return 'Sargent' in roles


def _get_notification_prefs(soldier: Soldier, db) -> dict:
    """Get notification preferences with defaults based on role.
    Commander (in command chain): both True by default.
    Sargent (not in chain): both False by default.
    """
    config = ConfigService(db).get_config()
    chain = (config.command_chain or []) if config else []
    is_in_chain = soldier.id in chain

    defaults = {
        'soldier_reports': is_in_chain,
        'gear_changes': is_in_chain,
    }

    prefs = soldier.notification_prefs
    if prefs and isinstance(prefs, dict):
        return {k: prefs.get(k, v) for k, v in defaults.items()}
    return defaults


async def _notify_privileged(client, db, message: str, category: str,
                              exclude_soldier_id: int | None = None):
    """Send a notification to all privileged soldiers who have the category enabled.
    category: 'soldier_reports' | 'gear_changes'
    Schedule changes and UNCOVERED alerts are always-on (use direct sends).
    """
    config = ConfigService(db).get_config()
    if not config:
        return
    chain = config.command_chain or []

    soldier_svc = SoldierService(db)
    notified = set()

    # Check all active soldiers for privilege
    for s in soldier_svc.list_notifiable_soldiers():
        if exclude_soldier_id and s.id == exclude_soldier_id:
            continue
        if s.matrix_id in notified:
            continue

        is_priv = s.id in chain or 'Sargent' in (s.role or [])
        if not is_priv:
            continue

        prefs = _get_notification_prefs(s, db)
        if prefs.get(category, False):
            await _send_to_user(client, s.matrix_id, message)
            notified.add(s.matrix_id)


# ── Swap timeout tasks: requester_matrix_id -> asyncio.Task ──────────────────
_swap_timeout_tasks: dict[str, asyncio.Task] = {}

# ── Ongoing unplanned task check-in tasks: matrix_id -> asyncio.Task ─────────
_ongoing_checkin_tasks: dict[str, asyncio.Task] = {}


def _parse_hhmm(text: str) -> time | None:
    """Parse HH:MM (24h) from user input. Returns time or None."""
    text = text.strip().replace('.', ':')
    parts = text.split(':')
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return time(h, m)
    return None


def _parse_ddmm_hhmm(text: str) -> datetime | None:
    """Parse 'DD/MM HH:MM' into a datetime (current year). Returns None on failure."""
    text = text.strip()
    parts = text.split()
    if len(parts) != 2:
        return None
    date_part, time_part = parts
    date_parts = date_part.split('/')
    if len(date_parts) != 2:
        return None
    try:
        day, month = int(date_parts[0]), int(date_parts[1])
    except ValueError:
        return None
    t_parsed = _parse_hhmm(time_part)
    if not t_parsed:
        return None
    year = datetime.now().year
    try:
        return datetime(year, month, day, t_parsed.hour, t_parsed.minute)
    except ValueError:
        return None


def _smart_date_for_time(t: time) -> date:
    """Given a time, pick today or yesterday based on whether it's in the future."""
    now = datetime.now()
    candidate = datetime.combine(date.today(), t)
    if candidate > now + timedelta(hours=1):
        return date.today() - timedelta(days=1)
    return date.today()

# ── Room cache: matrix_id -> room_id ──────────────────────────────────────────
_dm_rooms: dict[str, str] = {}


async def _get_or_create_dm(client: AsyncClient, target_matrix_id: str) -> str | None:
    """Get existing DM room or create one. Returns room_id or None."""
    if target_matrix_id in _dm_rooms:
        return _dm_rooms[target_matrix_id]

    for room_id, room in client.rooms.items():
        members = room.users
        if len(members) == 2 and target_matrix_id in members:
            _dm_rooms[target_matrix_id] = room_id
            return room_id

    resp = await client.room_create(
        is_direct=True,
        invite=[target_matrix_id],
        initial_state=[
            {
                "type": "m.room.encryption",
                "state_key": "",
                "content": {"algorithm": "m.megolm.v1.aes-sha2"},
            }
        ],
    )
    if isinstance(resp, RoomCreateResponse):
        _dm_rooms[target_matrix_id] = resp.room_id
        return resp.room_id
    logger.warning("Failed to create DM room for %s: %s", target_matrix_id, resp)
    return None


async def _send(client: AsyncClient, room_id: str, text: str):
    """Send a plaintext message to a room (E2EE is handled transparently)."""
    try:
        await client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": text},
        )
    except Exception:
        logger.exception("Failed to send message to %s", room_id)


async def _send_to_user(client: AsyncClient, matrix_id: str, text: str) -> bool:
    """Send a DM to a user by matrix ID. Returns True on success."""
    room_id = await _get_or_create_dm(client, matrix_id)
    if not room_id:
        return False
    await _send(client, room_id, text)
    return True


# ── Navigation helper ─────────────────────────────────────────────────────────

def _nav_footer(lang: str) -> str:
    return (
        f"\n{t(lang, 'nav_prev')}\n"
        f"{t(lang, 'nav_next')}\n"
        f"{t(lang, 'nav_today')}\n"
        f"{t(lang, 'nav_back')}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER — state machine router
# ═════════════════════════════════════════════════════════════════════════════

async def on_message(client: AsyncClient, room: MatrixRoom, event: RoomMessageText):
    """Main entry point for all incoming messages."""
    if event.sender == client.user_id:
        return

    if hasattr(client, '_boot_ts') and event.server_timestamp < client._boot_ts:
        return

    sender = event.sender
    body = event.body.strip()

    with bot_session() as db:
        try:
            soldier = SoldierService(db).get_soldier_by_matrix_id(sender)
            if not soldier:
                await _send(client, room.room_id, t('en', 'unrecognized'))
                return

            us = _get_state(sender)

            # First-time user: check language preference
            if us["state"] is None:
                lang = soldier.preferred_language or None
                if lang:
                    us["lang"] = lang
                    _go_main_menu(sender)
                    await _show_main_menu(client, room, db, soldier, sender)
                    return
                else:
                    us["state"] = "language_select"
                    await _send(client, room.room_id, t('en', 'language_select'))
                    return

            lang = us["lang"]

            # Route to state handler
            handler = _STATE_HANDLERS.get(us["state"])
            if handler:
                await handler(client, room, db, soldier, sender, body)
            else:
                # Unknown state — reset to main menu
                _go_main_menu(sender)
                await _show_main_menu(client, room, db, soldier, sender)

        except Exception:
            logger.exception("Handler error for %s", sender)
            lang = _get_state(sender).get("lang", "en")
            await _send(client, room.room_id, t(lang, 'error'))
            _go_main_menu(sender)


# ═════════════════════════════════════════════════════════════════════════════
# STATE HANDLERS
# ═════════════════════════════════════════════════════════════════════════════

# ── language_select ────────────────────────────────────────────────────────────

async def _handle_language_select(client, room, db, soldier, sender, body):
    if body == "1":
        lang = "en"
    elif body == "2":
        lang = "he"
    else:
        await _send(client, room.room_id, t('en', 'language_select'))
        return

    # Save to DB
    SoldierService(db).update_soldier(soldier.id, preferred_language=lang)
    us = _get_state(sender)
    us["lang"] = lang
    await _send(client, room.room_id, t(lang, 'language_saved'))
    _go_main_menu(sender)
    await _show_main_menu(client, room, db, soldier, sender)


# ── main_menu ─────────────────────────────────────────────────────────────────

async def _show_main_menu(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    header = t(lang, 'main_menu_header', name=display_name(soldier))
    options = t(lang, 'main_menu_options')

    is_priv = _is_privileged(soldier, db)
    if is_priv:
        options += "\n" + t(lang, 'main_menu_commander', n=11)
        options += "\n" + t(lang, 'main_menu_notifications', n=12)

    await _send(client, room.room_id, f"{header}\n\n{options}")


async def _handle_main_menu(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]

    handlers = {
        "1": _enter_my_schedule,
        "2": _enter_team_schedule,
        "3": _enter_tasks_view,
        "4": _enter_swap_pick_assignment,
        "5": _enter_unplanned,
        "6": _enter_report_issue,
        "7": _enter_my_gear,
        "8": _enter_team_gear,
        "9": _enter_my_stats,
        "10": _enter_change_language,
    }

    if body == "11" and _is_privileged(soldier, db):
        await _enter_commander_menu(client, room, db, soldier, sender)
        return

    if body == "12" and _is_privileged(soldier, db):
        await _enter_notification_settings(client, room, db, soldier, sender)
        return

    fn = handlers.get(body)
    if fn:
        await fn(client, room, db, soldier, sender)
    else:
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        await _show_main_menu(client, room, db, soldier, sender)


# ── my_schedule ───────────────────────────────────────────────────────────────

async def _enter_my_schedule(client, room, db, soldier, sender):
    _set_state(sender, "my_schedule", current_date=date.today().isoformat())
    await _show_my_schedule(client, room, db, soldier, sender)


async def _show_my_schedule(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    current_date = date.fromisoformat(us["data"]["current_date"])

    config = ConfigService(db).get_config()
    ns = config.night_start_hour if config else 23
    ne = config.night_end_hour if config else 7

    assignments = get_day_schedule(db, current_date)
    my_asgn = [a for a in assignments if a.soldier_id == soldier.id]

    date_str = current_date.strftime("%d/%m/%Y")
    lines = [t(lang, 'my_schedule_header', date=date_str)]

    day_asgn = []
    night_asgn = []
    for a in my_asgn:
        if _is_night_hour(a.start_time.hour, ns, ne):
            night_asgn.append(a)
        else:
            day_asgn.append(a)

    lines.append(f"  {t(lang, 'day_header')}")
    if day_asgn:
        for a in day_asgn:
            lines.append(t(lang, 'assignment_line',
                           task=task_display(a.task),
                           start=a.start_time.strftime('%H:%M'),
                           end=a.end_time.strftime('%H:%M')))
    else:
        lines.append(f"    {t(lang, 'no_assignments')}")

    lines.append(f"  {t(lang, 'night_header')}")
    if night_asgn:
        for a in night_asgn:
            lines.append(t(lang, 'assignment_line',
                           task=task_display(a.task),
                           start=a.start_time.strftime('%H:%M'),
                           end=a.end_time.strftime('%H:%M')))
    else:
        lines.append(f"    {t(lang, 'no_assignments')}")

    lines.append(_nav_footer(lang))
    await _send(client, room.room_id, "\n".join(lines))


async def _handle_my_schedule(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    current_date = date.fromisoformat(us["data"]["current_date"])

    if body == "1":
        new_date = current_date - timedelta(days=1)
    elif body == "2":
        new_date = current_date + timedelta(days=1)
    elif body == "3":
        new_date = date.today()
    elif body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return
    else:
        lang = us["lang"]
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        await _show_my_schedule(client, room, db, soldier, sender)
        return

    us["data"]["current_date"] = new_date.isoformat()
    await _show_my_schedule(client, room, db, soldier, sender)


# ── team_schedule ─────────────────────────────────────────────────────────────

async def _enter_team_schedule(client, room, db, soldier, sender):
    _set_state(sender, "team_schedule", current_date=date.today().isoformat())
    await _show_team_schedule(client, room, db, soldier, sender)


async def _show_team_schedule(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    current_date = date.fromisoformat(us["data"]["current_date"])

    config = ConfigService(db).get_config()
    ns = config.night_start_hour if config else 23
    ne = config.night_end_hour if config else 7

    assignments = get_day_schedule(db, current_date)

    date_str = current_date.strftime("%d/%m/%Y")
    lines = [t(lang, 'team_schedule_header', date=date_str)]

    # Group by task, then split day/night
    grouped: dict[int, list[TaskAssignment]] = {}
    for a in assignments:
        grouped.setdefault(a.task_id, []).append(a)

    day_lines = []
    night_lines = []
    for task_id, task_asgns in grouped.items():
        tk = task_asgns[0].task
        soldiers_str = ", ".join(display_name(a.soldier) for a in task_asgns if a.soldier)
        start_str = task_asgns[0].start_time.strftime('%H:%M')
        end_str = task_asgns[-1].end_time.strftime('%H:%M')
        line = t(lang, 'team_assignment_line',
                 task=task_display(tk), start=start_str, end=end_str,
                 soldiers=soldiers_str)
        if _is_night_hour(task_asgns[0].start_time.hour, ns, ne):
            night_lines.append(line)
        else:
            day_lines.append(line)

    lines.append(f"  {t(lang, 'day_header')}")
    if day_lines:
        lines.extend(day_lines)
    else:
        lines.append(f"    {t(lang, 'no_assignments')}")

    lines.append(f"  {t(lang, 'night_header')}")
    if night_lines:
        lines.extend(night_lines)
    else:
        lines.append(f"    {t(lang, 'no_assignments')}")

    lines.append(_nav_footer(lang))
    await _send(client, room.room_id, "\n".join(lines))


async def _handle_team_schedule(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    current_date = date.fromisoformat(us["data"]["current_date"])

    if body == "1":
        new_date = current_date - timedelta(days=1)
    elif body == "2":
        new_date = current_date + timedelta(days=1)
    elif body == "3":
        new_date = date.today()
    elif body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return
    else:
        lang = us["lang"]
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        await _show_team_schedule(client, room, db, soldier, sender)
        return

    us["data"]["current_date"] = new_date.isoformat()
    await _show_team_schedule(client, room, db, soldier, sender)


# ── tasks_view ────────────────────────────────────────────────────────────────

async def _enter_tasks_view(client, room, db, soldier, sender):
    _set_state(sender, "tasks_view", current_date=date.today().isoformat())
    await _show_tasks_view(client, room, db, soldier, sender)


async def _show_tasks_view(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    current_date = date.fromisoformat(us["data"]["current_date"])

    date_str = current_date.strftime("%d/%m/%Y")
    lines = [t(lang, 'tasks_header', date=date_str)]

    task_svc = TaskService(db)
    tasks = task_svc.get_tasks_for_date(current_date)

    if not tasks:
        lines.append(f"  {t(lang, 'no_assignments')}")
    else:
        assignments = get_day_schedule(db, current_date)
        # Count assignments per task
        asgn_by_task: dict[int, int] = defaultdict(int)
        for a in assignments:
            asgn_by_task[a.task_id] += 1

        for tk in tasks:
            filled = asgn_by_task.get(tk.id, 0)
            required = tk.required_count or 1
            start_str = tk.start_time.strftime('%H:%M')
            end_str = tk.end_time.strftime('%H:%M')
            if filled >= required:
                lines.append(t(lang, 'task_covered',
                               task=task_display(tk), start=start_str,
                               end=end_str, filled=filled, required=required))
            else:
                lines.append(t(lang, 'task_uncovered',
                               task=task_display(tk), start=start_str,
                               end=end_str, filled=filled, required=required))

    lines.append(_nav_footer(lang))
    await _send(client, room.room_id, "\n".join(lines))


async def _handle_tasks_view(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    current_date = date.fromisoformat(us["data"]["current_date"])

    if body == "1":
        new_date = current_date - timedelta(days=1)
    elif body == "2":
        new_date = current_date + timedelta(days=1)
    elif body == "3":
        new_date = date.today()
    elif body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return
    else:
        lang = us["lang"]
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        await _show_tasks_view(client, room, db, soldier, sender)
        return

    us["data"]["current_date"] = new_date.isoformat()
    await _show_tasks_view(client, room, db, soldier, sender)


# ── my_stats ──────────────────────────────────────────────────────────────────

def _split_day_night_hours(asgn: TaskAssignment, night_start: int, night_end: int) -> tuple[float, float]:
    """Split an assignment's duration into day-hours and night-hours."""
    if not asgn.start_time or not asgn.end_time:
        return 0.0, 0.0
    day_h = 0.0
    night_h = 0.0
    cursor = asgn.start_time
    step = timedelta(minutes=15)
    while cursor < asgn.end_time:
        slice_end = min(cursor + step, asgn.end_time)
        h = (slice_end - cursor).total_seconds() / 3600
        if _is_night_hour(cursor.hour, night_start, night_end):
            night_h += h
        else:
            day_h += h
        cursor = slice_end
    return day_h, night_h


async def _enter_my_stats(client, room, db, soldier, sender):
    _set_state(sender, "my_stats", weighted=True)
    await _show_my_stats(client, room, db, soldier, sender)


async def _show_my_stats(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    weighted = us["data"].get("weighted", True)

    config = ConfigService(db).get_config()
    ns = config.night_start_hour if config else 23
    ne = config.night_end_hour if config else 7

    # Determine period
    period_start = None
    period_end = None
    if config:
        period_start = config.reserve_period_start
        period_end = config.reserve_period_end

    sched_svc = ScheduleService(db)
    all_asgn = sched_svc.get_all_active_assignments(
        start=period_start, end=period_end,
    )

    active_soldiers = SoldierService(db).list_active_soldiers()
    active_ids = [s.id for s in active_soldiers]
    n_active = len(active_ids)

    # Per-soldier hours
    per_soldier_day: dict[int, float] = defaultdict(float)
    per_soldier_night: dict[int, float] = defaultdict(float)
    per_soldier_total: dict[int, float] = defaultdict(float)

    for a in all_asgn:
        dh, nh = _split_day_night_hours(a, ns, ne)
        per_soldier_day[a.soldier_id] += dh
        per_soldier_night[a.soldier_id] += nh
        per_soldier_total[a.soldier_id] += dh + nh

    my_day = per_soldier_day.get(soldier.id, 0.0)
    my_night = per_soldier_night.get(soldier.id, 0.0)
    my_total = per_soldier_total.get(soldier.id, 0.0)

    if weighted and period_start and period_end:
        # Weighted mode: use presence fractions
        from src.domain.presence_calc import compute_domain_fractions

        day_frac_sum: dict[int, float] = {sid: 0.0 for sid in active_ids}
        night_frac_sum: dict[int, float] = {sid: 0.0 for sid in active_ids}
        combined_frac_sum: dict[int, float] = {sid: 0.0 for sid in active_ids}

        cur = period_start.date() if isinstance(period_start, datetime) else period_start
        end_d = period_end.date() if isinstance(period_end, datetime) else period_end
        while cur < end_d:
            df, nf = compute_domain_fractions(db, active_ids, cur, ns, ne)
            for sid in active_ids:
                day_frac_sum[sid] += df.get(sid, 0.0)
                night_frac_sum[sid] += nf.get(sid, 0.0)
                combined_frac_sum[sid] += df.get(sid, 0.0) + nf.get(sid, 0.0)
            cur += timedelta(days=1)

        # Weighted averages
        total_hours_sum = sum(per_soldier_total.get(sid, 0.0) for sid in active_ids)
        total_frac_sum = sum(combined_frac_sum.get(sid, 0.0) for sid in active_ids)
        avg_total = total_hours_sum / total_frac_sum if total_frac_sum > 0.001 else 0.0

        total_day_sum = sum(per_soldier_day.get(sid, 0.0) for sid in active_ids)
        total_day_frac = sum(day_frac_sum.get(sid, 0.0) for sid in active_ids)
        avg_day = total_day_sum / total_day_frac if total_day_frac > 0.001 else 0.0

        total_night_sum = sum(per_soldier_night.get(sid, 0.0) for sid in active_ids)
        total_night_frac = sum(night_frac_sum.get(sid, 0.0) for sid in active_ids)
        avg_night = total_night_sum / total_night_frac if total_night_frac > 0.001 else 0.0

        # Per-present-day values for this soldier
        my_frac = combined_frac_sum.get(soldier.id, 0.0)
        my_rate = my_total / my_frac if my_frac > 0.001 else 0.0
        my_day_frac = day_frac_sum.get(soldier.id, 0.0)
        my_day_rate = my_day / my_day_frac if my_day_frac > 0.001 else 0.0
        my_night_frac = night_frac_sum.get(soldier.id, 0.0)
        my_night_rate = my_night / my_night_frac if my_night_frac > 0.001 else 0.0

        display_total = my_rate
        display_avg = avg_total
        display_day = my_day_rate
        display_night = my_night_rate
        avg_day_val = avg_day
        avg_night_val = avg_night

        # Rank by rate
        rates = {}
        for sid in active_ids:
            f = combined_frac_sum.get(sid, 0.0)
            rates[sid] = per_soldier_total.get(sid, 0.0) / f if f > 0.001 else 0.0
        my_rank_val = rates.get(soldier.id, 0.0)
        rank = sum(1 for r in rates.values() if r > my_rank_val + 0.0001) + 1

        mode_str = t(lang, 'stats_mode_weighted')
        toggle_key = 'stats_toggle_absolute'
    else:
        # Absolute mode
        avg_total = sum(per_soldier_total.get(sid, 0.0) for sid in active_ids) / n_active if n_active else 0
        avg_day_val = sum(per_soldier_day.get(sid, 0.0) for sid in active_ids) / n_active if n_active else 0
        avg_night_val = sum(per_soldier_night.get(sid, 0.0) for sid in active_ids) / n_active if n_active else 0

        display_total = my_total
        display_avg = avg_total
        display_day = my_day
        display_night = my_night

        # Rank by total hours
        rank = sum(1 for sid in active_ids
                   if per_soldier_total.get(sid, 0.0) > my_total + 0.0001) + 1

        mode_str = t(lang, 'stats_mode_absolute')
        toggle_key = 'stats_toggle_weighted'

    # Format diff strings
    day_diff = display_day - avg_day_val
    night_diff = display_night - avg_night_val
    day_diff_str = f"+{day_diff:.1f}h above avg" if day_diff >= 0 else f"{day_diff:.1f}h below avg"
    night_diff_str = f"+{night_diff:.1f}h above avg" if night_diff >= 0 else f"{night_diff:.1f}h below avg"

    lines = [
        t(lang, 'stats_header', mode=mode_str),
        t(lang, 'stats_total', total=f"{display_total:.1f}", avg=f"{display_avg:.1f}"),
        t(lang, 'stats_day', hours=f"{display_day:.1f}", diff=day_diff_str),
        t(lang, 'stats_night', hours=f"{display_night:.1f}", diff=night_diff_str),
        t(lang, 'stats_rank', rank=ordinal(lang, rank), total=n_active),
        "",
        t(lang, toggle_key),
        t(lang, 'nav_back'),
    ]
    await _send(client, room.room_id, "\n".join(lines))


async def _handle_my_stats(client, room, db, soldier, sender, body):
    us = _get_state(sender)

    if body == "1":
        us["data"]["weighted"] = not us["data"].get("weighted", True)
        await _show_my_stats(client, room, db, soldier, sender)
    elif body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
    else:
        lang = us["lang"]
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        await _show_my_stats(client, room, db, soldier, sender)


# ── change_language ───────────────────────────────────────────────────────────

async def _enter_change_language(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    us["state"] = "change_language"
    await _send(client, room.room_id, t(lang, 'change_language'))


async def _handle_change_language(client, room, db, soldier, sender, body):
    if body == "1":
        lang = "en"
    elif body == "2":
        lang = "he"
    else:
        us = _get_state(sender)
        cur_lang = us["lang"]
        await _send(client, room.room_id, t(cur_lang, 'change_language'))
        return

    SoldierService(db).update_soldier(soldier.id, preferred_language=lang)
    us = _get_state(sender)
    us["lang"] = lang
    await _send(client, room.room_id, t(lang, 'language_saved'))
    _go_main_menu(sender)
    await _show_main_menu(client, room, db, soldier, sender)


# ── swap_pick_assignment ─────────────────────────────────────────────────────

async def _enter_swap_pick_assignment(client, room, db, soldier, sender):
    assignments = ScheduleService(db).get_soldier_upcoming_assignments(
        soldier.id, datetime.now(),
    )
    if not assignments:
        lang = _get_state(sender)["lang"]
        await _send(client, room.room_id, t(lang, 'swap_no_assignments'))
        return

    _set_state(sender, "swap_pick_assignment",
               assignments=[a.id for a in assignments])
    await _show_swap_pick_assignment(client, room, db, soldier, sender, assignments)


async def _show_swap_pick_assignment(client, room, db, soldier, sender, assignments=None):
    us = _get_state(sender)
    lang = us["lang"]
    if assignments is None:
        sched_svc = ScheduleService(db)
        assignments = [
            sched_svc.get_assignment(aid)
            for aid in us["data"]["assignments"]
        ]
        assignments = [a for a in assignments if a is not None]

    lines = []
    for i, a in enumerate(assignments, 1):
        tk = a.task
        lines.append(
            f"  {i}. {task_display(tk)} — "
            f"{a.start_time.strftime('%a %d/%m %H:%M')}-{a.end_time.strftime('%H:%M')}"
        )
    await _send(client, room.room_id,
                t(lang, 'swap_pick_assignment', lines="\n".join(lines)))


async def _handle_swap_pick_assignment(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]

    if body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return

    try:
        idx = int(body) - 1
    except ValueError:
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    assignment_ids = us["data"].get("assignments", [])
    if idx < 0 or idx >= len(assignment_ids):
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    assignment_id = assignment_ids[idx]
    assignment = ScheduleService(db).get_assignment(assignment_id)
    if not assignment:
        await _send(client, room.room_id, t(lang, 'error'))
        _go_main_menu(sender)
        return

    # Find swap candidates
    candidates = SoldierService(db).get_swap_candidates(
        soldier.id, assignment.start_time, assignment.end_time,
    )
    # Exclude soldiers already assigned in this window
    busy_ids = ScheduleService(db).get_assigned_soldier_ids_in_window(
        assignment.start_time, assignment.end_time,
        exclude_assignment_id=assignment.id,
    )
    candidates = [c for c in candidates if c.id not in busy_ids]

    if not candidates:
        await _send(client, room.room_id, t(lang, 'swap_no_candidates'))
        return

    # Compute day/night stats for context
    config = ConfigService(db).get_config()
    ns = config.night_start_hour if config else 23
    ne = config.night_end_hour if config else 7
    is_night = _is_night_hour(assignment.start_time.hour, ns, ne)
    domain = "\U0001f319" if is_night else "\U0001f31e"

    sched_svc = ScheduleService(db)
    period_start = config.reserve_period_start if config else None
    period_end = config.reserve_period_end if config else None
    all_asgn = sched_svc.get_all_active_assignments(start=period_start, end=period_end)

    # Per-soldier domain hours
    per_soldier_hours: dict[int, float] = defaultdict(float)
    for a in all_asgn:
        dh, nh = _split_day_night_hours(a, ns, ne)
        per_soldier_hours[a.soldier_id] += nh if is_night else dh

    active_soldiers = SoldierService(db).list_active_soldiers()
    n_active = len(active_soldiers)
    avg_hours = sum(per_soldier_hours.get(s.id, 0.0) for s in active_soldiers) / n_active if n_active else 0

    lines = []
    candidate_ids = []
    for i, c in enumerate(candidates, 1):
        h = per_soldier_hours.get(c.id, 0.0)
        diff = h - avg_hours
        diff_str = f"+{diff:.1f}h" if diff >= 0 else f"{diff:.1f}h"
        lines.append(t(lang, 'swap_candidate_line',
                       n=i, name=display_name(c), domain=domain, diff=diff_str))
        candidate_ids.append(c.id)

    _set_state(sender, "swap_pick_soldier",
               selected_assignment_id=assignment_id,
               candidate_ids=candidate_ids)
    await _send(client, room.room_id,
                t(lang, 'swap_pick_soldier', lines="\n".join(lines)))


# ── swap_pick_soldier ────────────────────────────────────────────────────────

async def _handle_swap_pick_soldier(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]

    if body == "0":
        # Go back to assignment pick
        await _enter_swap_pick_assignment(client, room, db, soldier, sender)
        return

    try:
        idx = int(body) - 1
    except ValueError:
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    candidate_ids = us["data"].get("candidate_ids", [])
    if idx < 0 or idx >= len(candidate_ids):
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    target_id = candidate_ids[idx]
    assignment_id = us["data"]["selected_assignment_id"]
    assignment = ScheduleService(db).get_assignment(assignment_id)
    target_soldier = SoldierService(db).get_soldier(target_id)

    if not assignment or not target_soldier:
        await _send(client, room.room_id, t(lang, 'error'))
        _go_main_menu(sender)
        return

    config = ConfigService(db).get_config()
    timeout_minutes = config.swap_approval_timeout_minutes if config else 15

    task_name = task_display(assignment.task)
    date_str = assignment.start_time.strftime('%d/%m')
    start_str = assignment.start_time.strftime('%H:%M')
    end_str = assignment.end_time.strftime('%H:%M')

    # Set requester to waiting state
    _set_state(sender, "swap_waiting",
               assignment_id=assignment_id,
               target_soldier_id=target_id,
               target_matrix_id=target_soldier.matrix_id)

    # Send waiting message to requester
    await _send(client, room.room_id, t(lang, 'swap_waiting',
                target=display_name(target_soldier),
                task=task_name, date=date_str, start=start_str, end=end_str,
                requester=display_name(soldier), timeout=timeout_minutes))

    # Interrupt target and deliver swap request
    target_mx = target_soldier.matrix_id
    if target_mx:
        target_us = _get_state(target_mx)
        # Save target's current state for restoration
        _set_state(target_mx, "swap_respond",
                   previous_state=target_us["state"],
                   previous_data=dict(target_us["data"]),
                   assignment_id=assignment_id,
                   requester_matrix_id=sender,
                   requester_soldier_id=soldier.id)

        target_lang = target_us["lang"]
        await _send_to_user(client, target_mx,
                            t(target_lang, 'swap_request_incoming',
                              requester=display_name(soldier),
                              task=task_name, date=date_str,
                              start=start_str, end=end_str))

    # Start timeout task
    async def _swap_timeout():
        await asyncio.sleep(timeout_minutes * 60)
        # Timeout expired — reset both parties
        with bot_session() as timeout_db:
            req_soldier = SoldierService(timeout_db).get_soldier_by_matrix_id(sender)
            tgt_soldier = SoldierService(timeout_db).get_soldier(target_id)

            req_lang = _get_state(sender).get("lang", "en")
            _go_main_menu(sender)
            await _send_to_user(client, sender,
                                t(req_lang, 'swap_timeout_requester',
                                  target=display_name(tgt_soldier) if tgt_soldier else "?",
                                  timeout=timeout_minutes))

            if target_mx:
                tgt_us = _get_state(target_mx)
                tgt_lang = tgt_us.get("lang", "en")
                # Restore target's previous state
                prev_state = tgt_us["data"].get("previous_state")
                prev_data = tgt_us["data"].get("previous_data", {})
                if prev_state:
                    tgt_us["state"] = prev_state
                    tgt_us["data"] = prev_data
                else:
                    _go_main_menu(target_mx)
                await _send_to_user(client, target_mx,
                                    t(tgt_lang, 'swap_timeout_target',
                                      requester=display_name(req_soldier) if req_soldier else "?"))

        _swap_timeout_tasks.pop(sender, None)

    # Cancel any existing timeout for this requester
    old_task = _swap_timeout_tasks.pop(sender, None)
    if old_task:
        old_task.cancel()

    _swap_timeout_tasks[sender] = asyncio.create_task(_swap_timeout())


# ── swap_waiting (requester waits) ───────────────────────────────────────────

async def _handle_swap_waiting(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]

    if body == "0":
        # Cancel the swap
        target_mx = us["data"].get("target_matrix_id")
        target_id = us["data"].get("target_soldier_id")

        # Cancel timeout
        timeout_task = _swap_timeout_tasks.pop(sender, None)
        if timeout_task:
            timeout_task.cancel()

        # Notify target and restore their state
        if target_mx:
            tgt_us = _get_state(target_mx)
            tgt_lang = tgt_us.get("lang", "en")
            prev_state = tgt_us["data"].get("previous_state")
            prev_data = tgt_us["data"].get("previous_data", {})
            if prev_state:
                tgt_us["state"] = prev_state
                tgt_us["data"] = prev_data
            else:
                _go_main_menu(target_mx)
            await _send_to_user(client, target_mx,
                                t(tgt_lang, 'swap_cancelled_target',
                                  requester=display_name(soldier)))

        _go_main_menu(sender)
        await _send(client, room.room_id, t(lang, 'swap_cancelled'))
        await _show_main_menu(client, room, db, soldier, sender)
        return

    # Any other input — just remind them they're waiting
    await _send(client, room.room_id, t(lang, 'invalid_option'))


# ── swap_respond (target accepts/declines) ──────────────────────────────────

async def _handle_swap_respond(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    assignment_id = us["data"].get("assignment_id")
    requester_mx = us["data"].get("requester_matrix_id")
    requester_soldier_id = us["data"].get("requester_soldier_id")
    prev_state = us["data"].get("previous_state")
    prev_data = us["data"].get("previous_data", {})

    assignment = ScheduleService(db).get_assignment(assignment_id) if assignment_id else None
    requester = SoldierService(db).get_soldier(requester_soldier_id) if requester_soldier_id else None

    def _restore_target():
        if prev_state:
            us["state"] = prev_state
            us["data"] = prev_data
        else:
            _go_main_menu(sender)

    if body == "1":
        # Accept
        # Cancel timeout
        if requester_mx:
            timeout_task = _swap_timeout_tasks.pop(requester_mx, None)
            if timeout_task:
                timeout_task.cancel()

        if assignment:
            result = ScheduleService(db).swap_assignment(assignment_id, soldier.id)
            task_name = task_display(assignment.task)
            date_str = assignment.start_time.strftime('%d/%m')
            start_str = assignment.start_time.strftime('%H:%M')
            end_str = assignment.end_time.strftime('%H:%M')

            # Notify requester
            if requester_mx:
                req_lang = _get_state(requester_mx).get("lang", "en")
                _go_main_menu(requester_mx)
                await _send_to_user(client, requester_mx,
                                    t(req_lang, 'swap_accepted',
                                      task=task_name, date=date_str,
                                      start=start_str, end=end_str,
                                      target=display_name(soldier)))

            # Notify target (self)
            await _send(client, room.room_id,
                        t(lang, 'swap_accepted',
                          task=task_name, date=date_str,
                          start=start_str, end=end_str,
                          target=display_name(soldier)))

        _restore_target()
        return

    elif body == "2":
        # Decline
        if requester_mx:
            timeout_task = _swap_timeout_tasks.pop(requester_mx, None)
            if timeout_task:
                timeout_task.cancel()

            req_lang = _get_state(requester_mx).get("lang", "en")
            _go_main_menu(requester_mx)
            await _send_to_user(client, requester_mx,
                                t(req_lang, 'swap_declined',
                                  target=display_name(soldier)))

        await _send(client, room.room_id,
                    t(lang, 'swap_declined_target',
                      requester=display_name(requester) if requester else "?"))
        _restore_target()
        return

    else:
        await _send(client, room.room_id, t(lang, 'invalid_option'))


# ── my_gear ──────────────────────────────────────────────────────────────────

def _format_gear_item(lang: str, key: str, n: int, item) -> str:
    qty = f" (\u00d7{item.quantity})" if item.quantity and item.quantity > 1 else ""
    serial = f" — S/N: {item.serial_number}" if item.serial_number else ""
    return t(lang, key, n=n, name=item.item_name, quantity=qty, serial=serial)


async def _enter_my_gear(client, room, db, soldier, sender):
    _set_state(sender, "my_gear")
    await _show_my_gear(client, room, db, soldier, sender)


async def _show_my_gear(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    items = GearService(db).list_soldier_gear(soldier.id)

    lines = [t(lang, 'my_gear_header')]
    if not items:
        lines.append(f"  {t(lang, 'my_gear_empty')}")
    else:
        for i, item in enumerate(items, 1):
            lines.append(_format_gear_item(lang, 'my_gear_item', i, item))
    lines.append(t(lang, 'my_gear_actions'))
    await _send(client, room.room_id, "\n".join(lines))


async def _handle_my_gear(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    body_upper = body.strip().upper()

    if body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return

    if body_upper == "A":
        _set_state(sender, "my_gear_add", step="name")
        await _send(client, room.room_id, t(lang, 'my_gear_add_name'))
        return

    if body_upper == "R":
        items = GearService(db).list_soldier_gear(soldier.id)
        if not items:
            await _send(client, room.room_id, t(lang, 'my_gear_empty'))
            return
        item_lines = []
        item_ids = []
        for i, item in enumerate(items, 1):
            item_lines.append(_format_gear_item(lang, 'my_gear_item', i, item))
            item_ids.append(item.id)
        _set_state(sender, "my_gear_remove", gear_ids=item_ids)
        await _send(client, room.room_id,
                    t(lang, 'my_gear_remove_prompt', lines="\n".join(item_lines)))
        return

    await _send(client, room.room_id, t(lang, 'invalid_option'))


# ── my_gear_add ──────────────────────────────────────────────────────────────

async def _handle_my_gear_add(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    step = us["data"].get("step", "name")

    if body == "0":
        _set_state(sender, "my_gear")
        await _show_my_gear(client, room, db, soldier, sender)
        return

    if step == "name":
        us["data"]["gear_name"] = body
        us["data"]["step"] = "quantity"
        await _send(client, room.room_id, t(lang, 'my_gear_add_quantity'))
        return

    if step == "quantity":
        try:
            qty = int(body) if body.strip() else 1
        except ValueError:
            qty = 1
        if qty < 1:
            qty = 1
        us["data"]["gear_quantity"] = qty
        us["data"]["step"] = "serial"
        await _send(client, room.room_id, t(lang, 'my_gear_add_serial'))
        return

    if step == "serial":
        serial = body.strip() if body.strip() else None
        name = us["data"]["gear_name"]
        qty = us["data"].get("gear_quantity", 1)
        GearService(db).add_soldier_gear(soldier.id, name, qty, serial)
        serial_str = f" — S/N: {serial}" if serial else ""
        await _send(client, room.room_id,
                    t(lang, 'my_gear_added', name=name, quantity=qty, serial=serial_str))
        _set_state(sender, "my_gear")
        await _show_my_gear(client, room, db, soldier, sender)


# ── my_gear_remove ───────────────────────────────────────────────────────────

async def _handle_my_gear_remove(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]

    if body == "0":
        _set_state(sender, "my_gear")
        await _show_my_gear(client, room, db, soldier, sender)
        return

    try:
        idx = int(body) - 1
    except ValueError:
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    gear_ids = us["data"].get("gear_ids", [])
    if idx < 0 or idx >= len(gear_ids):
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    gear_svc = GearService(db)
    # Get item name before deleting
    item = db.query(GearItem).filter_by(id=gear_ids[idx]).first()
    item_name = item.item_name if item else "?"
    gear_svc.delete_soldier_gear(gear_ids[idx])
    await _send(client, room.room_id, t(lang, 'my_gear_removed', name=item_name))
    _set_state(sender, "my_gear")
    await _show_my_gear(client, room, db, soldier, sender)


# ── team_gear ────────────────────────────────────────────────────────────────

async def _enter_team_gear(client, room, db, soldier, sender):
    _set_state(sender, "team_gear")
    await _show_team_gear(client, room, db, soldier, sender)


async def _show_team_gear(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    items = GearService(db).list_team_gear()

    lines = [t(lang, 'team_gear_header')]
    if not items:
        lines.append(f"  {t(lang, 'team_gear_empty')}")
    else:
        for i, item in enumerate(items, 1):
            lines.append(_format_gear_item(lang, 'team_gear_item', i, item))
    lines.append(t(lang, 'team_gear_actions'))
    await _send(client, room.room_id, "\n".join(lines))


async def _handle_team_gear(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    body_upper = body.strip().upper()

    if body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return

    if body_upper == "A":
        _set_state(sender, "team_gear_add", step="name")
        await _send(client, room.room_id, t(lang, 'team_gear_add_name'))
        return

    if body_upper == "R":
        items = GearService(db).list_team_gear()
        if not items:
            await _send(client, room.room_id, t(lang, 'team_gear_empty'))
            return
        item_lines = []
        item_ids = []
        for i, item in enumerate(items, 1):
            item_lines.append(_format_gear_item(lang, 'team_gear_item', i, item))
            item_ids.append(item.id)
        _set_state(sender, "team_gear_remove", gear_ids=item_ids)
        await _send(client, room.room_id,
                    t(lang, 'team_gear_remove_prompt', lines="\n".join(item_lines)))
        return

    await _send(client, room.room_id, t(lang, 'invalid_option'))


# ── team_gear_add ────────────────────────────────────────────────────────────

async def _notify_privileged_gear(client, db, soldier, lang_key, **kwargs):
    """Send a team gear change notification to privileged users."""
    await _notify_privileged(client, db, t('en', lang_key, **kwargs),
                              category='gear_changes',
                              exclude_soldier_id=soldier.id)


async def _handle_team_gear_add(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    step = us["data"].get("step", "name")

    if body == "0":
        _set_state(sender, "team_gear")
        await _show_team_gear(client, room, db, soldier, sender)
        return

    if step == "name":
        us["data"]["gear_name"] = body
        us["data"]["step"] = "quantity"
        await _send(client, room.room_id, t(lang, 'team_gear_add_quantity'))
        return

    if step == "quantity":
        try:
            qty = int(body) if body.strip() else 1
        except ValueError:
            qty = 1
        if qty < 1:
            qty = 1
        us["data"]["gear_quantity"] = qty
        us["data"]["step"] = "serial"
        await _send(client, room.room_id, t(lang, 'team_gear_add_serial'))
        return

    if step == "serial":
        serial = body.strip() if body.strip() else None
        name = us["data"]["gear_name"]
        qty = us["data"].get("gear_quantity", 1)
        GearService(db).add_team_gear(name, qty, serial)
        serial_str = f" — S/N: {serial}" if serial else ""
        await _send(client, room.room_id,
                    t(lang, 'team_gear_added', name=name, quantity=qty, serial=serial_str))
        await _notify_privileged_gear(
            client, db, soldier, 'team_gear_commander_notify_add',
            soldier=display_name(soldier), name=name, quantity=qty)
        _set_state(sender, "team_gear")
        await _show_team_gear(client, room, db, soldier, sender)


# ── team_gear_remove ─────────────────────────────────────────────────────────

async def _handle_team_gear_remove(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]

    if body == "0":
        _set_state(sender, "team_gear")
        await _show_team_gear(client, room, db, soldier, sender)
        return

    try:
        idx = int(body) - 1
    except ValueError:
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    gear_ids = us["data"].get("gear_ids", [])
    if idx < 0 or idx >= len(gear_ids):
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    gear_svc = GearService(db)
    item = db.query(TeamGearItem).filter_by(id=gear_ids[idx]).first()
    item_name = item.item_name if item else "?"
    gear_svc.delete_team_gear(gear_ids[idx])
    await _send(client, room.room_id, t(lang, 'team_gear_removed', name=item_name))
    await _notify_commander_team_gear(
        client, db, soldier, 'team_gear_commander_notify_remove',
        soldier=display_name(soldier), name=item_name)
    _set_state(sender, "team_gear")
    await _show_team_gear(client, room, db, soldier, sender)


# ── report_issue ─────────────────────────────────────────────────────────────

async def _enter_report_issue(client, room, db, soldier, sender):
    _set_state(sender, "report_issue")
    lang = _get_state(sender)["lang"]
    await _send(client, room.room_id, t(lang, 'report_issue_prompt'))


async def _handle_report_issue(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]

    if body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return

    description = body.strip()
    if not description:
        await _send(client, room.room_id, t(lang, 'report_issue_prompt'))
        return

    # Create a NOTE request
    SoldierService(db).create_request(
        soldier.id, request_type='NOTE', description=description,
    )

    # Notify privileged users
    await _notify_privileged(
        client, db,
        t('en', 'report_issue_commander',
          soldier=display_name(soldier), description=description),
        category='soldier_reports',
        exclude_soldier_id=soldier.id)

    await _send(client, room.room_id,
                t(lang, 'report_issue_done', description=description))
    # Stay in report_issue state — user can type 0 to go back
    # (the "0. Back to menu" is shown in the done message)


# ── Shared role selection helper ─────────────────────────────────────────────

async def _show_role_select(client, room, db, sender, selected_roles: dict):
    """Show available roles for selection, with current selections displayed."""
    us = _get_state(sender)
    lang = us["lang"]
    roles = ConfigService(db).list_roles_for_picker()
    if not roles:
        return False  # No roles available

    role_names = [r.name for r in roles]
    us["data"]["available_roles"] = role_names

    lines = "\n".join(f"  {i}. {name}" for i, name in enumerate(role_names, 1))

    if selected_roles:
        current = ", ".join(f"{name} \u00d7{qty}" for name, qty in selected_roles.items())
        await _send(client, room.room_id,
                    t(lang, 'unplanned_role_select_with_current',
                      current=current, lines=lines))
    else:
        await _send(client, room.room_id,
                    t(lang, 'unplanned_role_select', lines=lines))
    return True


# ── unplanned task flow ──────────────────────────────────────────────────────

async def _enter_unplanned(client, room, db, soldier, sender):
    _set_state(sender, "unplanned_warning")
    us = _get_state(sender)
    us["data"] = {}
    lang = us["lang"]
    await _send(client, room.room_id, t(lang, 'unplanned_warning'))


async def _handle_unplanned_warning(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "1":
        _set_state(sender, "unplanned_describe")
        us["data"] = {}
        await _send(client, room.room_id, t(lang, 'unplanned_describe'))
    elif body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
    else:
        await _send(client, room.room_id, t(lang, 'invalid_option'))


async def _handle_unplanned_describe(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return
    us["data"]["description"] = body.strip()
    _set_state(sender, "unplanned_start_time")
    await _send(client, room.room_id, t(lang, 'unplanned_start_time'))


async def _handle_unplanned_start_time(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return
    parsed = _parse_hhmm(body)
    if not parsed:
        await _send(client, room.room_id, t(lang, 'unplanned_start_time_invalid'))
        return
    start_date = _smart_date_for_time(parsed)
    start_dt = datetime.combine(start_date, parsed)
    us["data"]["start_time"] = start_dt.isoformat()
    _set_state(sender, "unplanned_end_time")
    await _send(client, room.room_id, t(lang, 'unplanned_end_time'))


async def _handle_unplanned_end_time(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return

    if body.strip().lower() == "ongoing":
        us["data"]["ongoing"] = True
        us["data"]["end_time"] = None
        _set_state(sender, "unplanned_needs_more")
        await _send(client, room.room_id, t(lang, 'unplanned_needs_more'))
        return

    parsed = _parse_hhmm(body)
    if not parsed:
        await _send(client, room.room_id, t(lang, 'unplanned_start_time_invalid'))
        return

    start_dt = datetime.fromisoformat(us["data"]["start_time"])
    end_date = start_dt.date()
    end_dt = datetime.combine(end_date, parsed)
    # If end is before start, assume next day
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    us["data"]["end_time"] = end_dt.isoformat()
    us["data"]["ongoing"] = False
    _set_state(sender, "unplanned_needs_more")
    await _send(client, room.room_id, t(lang, 'unplanned_needs_more'))


async def _handle_unplanned_needs_more(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return
    if body == "1":
        us["data"]["required_count"] = 1
        _set_state(sender, "unplanned_roles_prompt")
        await _send(client, room.room_id, t(lang, 'unplanned_roles_prompt'))
    elif body == "2":
        _set_state(sender, "unplanned_how_many")
        await _send(client, room.room_id, t(lang, 'unplanned_how_many'))
    else:
        await _send(client, room.room_id, t(lang, 'invalid_option'))


async def _handle_unplanned_how_many(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return
    try:
        count = int(body)
    except ValueError:
        await _send(client, room.room_id, t(lang, 'commander_create_count_invalid'))
        return
    if count < 1:
        count = 1
    us["data"]["required_count"] = count
    _set_state(sender, "unplanned_roles_prompt")
    await _send(client, room.room_id, t(lang, 'unplanned_roles_prompt'))


async def _handle_unplanned_roles_prompt(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return
    if body == "1":
        us["data"]["selected_roles"] = {}
        await _show_unplanned_confirm(client, room, db, soldier, sender)
    elif body == "2":
        us["data"]["selected_roles"] = {}
        _set_state(sender, "unplanned_role_select")
        has_roles = await _show_role_select(client, room, db, sender, {})
        if not has_roles:
            us["data"]["selected_roles"] = {}
            await _show_unplanned_confirm(client, room, db, soldier, sender)
    else:
        await _send(client, room.room_id, t(lang, 'invalid_option'))


async def _handle_unplanned_role_select(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    selected_roles = us["data"].get("selected_roles", {})

    if body == "0":
        # Done selecting roles
        await _show_unplanned_confirm(client, room, db, soldier, sender)
        return

    available = us["data"].get("available_roles", [])
    try:
        idx = int(body) - 1
    except ValueError:
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return
    if idx < 0 or idx >= len(available):
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    role_name = available[idx]
    us["data"]["_pending_role"] = role_name
    _set_state(sender, "unplanned_role_quantity")
    await _send(client, room.room_id, t(lang, 'unplanned_role_quantity', role=role_name))


async def _handle_unplanned_role_quantity(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        _set_state(sender, "unplanned_role_select")
        await _show_role_select(client, room, db, sender, us["data"].get("selected_roles", {}))
        return
    try:
        qty = int(body)
    except ValueError:
        qty = 1
    if qty < 1:
        qty = 1
    role_name = us["data"].pop("_pending_role", "?")
    us["data"].setdefault("selected_roles", {})[role_name] = qty
    _set_state(sender, "unplanned_role_select")
    await _show_role_select(client, room, db, sender, us["data"]["selected_roles"])


async def _show_unplanned_confirm(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    d = us["data"]
    start_dt = datetime.fromisoformat(d["start_time"])
    end_str = "ongoing"
    if d.get("end_time"):
        end_dt = datetime.fromisoformat(d["end_time"])
        end_str = end_dt.strftime('%H:%M')
    roles = d.get("selected_roles", {})
    roles_str = ", ".join(f"{n} \u00d7{q}" for n, q in roles.items()) if roles else "Any"
    _set_state(sender, "unplanned_confirm")
    await _send(client, room.room_id, t(lang, 'unplanned_confirm',
        description=d["description"],
        date=start_dt.strftime('%d/%m'),
        start=start_dt.strftime('%H:%M'),
        end=end_str,
        count=d.get("required_count", 1),
        roles=roles_str))


async def _handle_unplanned_confirm(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return
    if body != "1":
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    d = us["data"]
    start_dt = datetime.fromisoformat(d["start_time"])
    ongoing = d.get("ongoing", False)
    if d.get("end_time"):
        end_dt = datetime.fromisoformat(d["end_time"])
    else:
        end_dt = datetime.now()  # ongoing: use now as temporary end

    description = d["description"]
    required_count = d.get("required_count", 1)
    selected_roles = d.get("selected_roles", {})

    # Build roles list for the task model
    roles_list = selected_roles if selected_roles else []

    # Create the task
    task_svc = TaskService(db)
    try:
        new_task = task_svc.create_task(
            real_title=f"[UNPLANNED] {description}",
            start_time=start_dt,
            end_time=end_dt,
            is_fractionable=False,
            required_count=required_count,
            required_roles_list=roles_list,
            base_weight=1.0,
            hardness=3,
            coverage_status='UNCOVERED',
        )
    except ValueError:
        # Coverage check may fail — create without validation
        new_task = Task(
            real_title=f"[UNPLANNED] {description}",
            start_time=start_dt,
            end_time=end_dt,
            is_fractionable=False,
            required_count=required_count,
            required_roles_list=roles_list if roles_list else [],
            base_weight=1.0,
            hardness=3,
            is_active=True,
            coverage_status='UNCOVERED',
        )
        task_svc.save_task(new_task)

    # Create assignment for the reporting soldier
    sched_svc = ScheduleService(db)
    sched_svc.add_assignment(
        task_id=new_task.id,
        soldier_id=soldier.id,
        start_time=start_dt,
        end_time=end_dt,
        weight=1.0,
        pin=True,
    )

    # Notify privileged users
    end_str = "ongoing" if ongoing else end_dt.strftime('%H:%M')
    notify_text = t('en', 'unplanned_commander_notify',
                    name=display_name(soldier),
                    description=description,
                    date=start_dt.strftime('%d/%m'),
                    start=start_dt.strftime('%H:%M'),
                    end=end_str)
    if required_count > 1:
        notify_text += t('en', 'unplanned_commander_needs_more',
                         count=required_count, remaining=required_count - 1)
    await _notify_privileged(client, db, notify_text,
                              category='soldier_reports',
                              exclude_soldier_id=soldier.id)

    await _send(client, room.room_id, t(lang, 'unplanned_created'))

    # If ongoing, start hourly check-in
    if ongoing:
        _start_ongoing_checkin(client, sender, soldier.id, new_task.id, description)

    _go_main_menu(sender)
    await _show_main_menu(client, room, db, soldier, sender)


def _start_ongoing_checkin(client, matrix_id, soldier_id, task_id, description):
    """Start an hourly check-in loop for an ongoing unplanned task."""
    old = _ongoing_checkin_tasks.pop(matrix_id, None)
    if old:
        old.cancel()

    async def _checkin_loop():
        while True:
            await asyncio.sleep(3600)  # 60 minutes
            us = _get_state(matrix_id)
            lang = us.get("lang", "en")

            # Save current state
            prev_state = us["state"]
            prev_data = dict(us["data"])

            _set_state(matrix_id, "unplanned_checkin",
                       checkin_task_id=task_id,
                       checkin_soldier_id=soldier_id,
                       checkin_description=description,
                       checkin_previous_state=prev_state,
                       checkin_previous_data=prev_data)

            await _send_to_user(client, matrix_id,
                                t(lang, 'unplanned_checkin', description=description))

    _ongoing_checkin_tasks[matrix_id] = asyncio.create_task(_checkin_loop())


async def _handle_unplanned_checkin(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    d = us["data"]
    task_id = d.get("checkin_task_id")
    description = d.get("checkin_description", "?")
    prev_state = d.get("checkin_previous_state")
    prev_data = d.get("checkin_previous_data", {})

    def _restore():
        if prev_state:
            us["state"] = prev_state
            us["data"] = prev_data
        else:
            _go_main_menu(sender)

    if body == "1":
        # Still ongoing
        await _send(client, room.room_id, t(lang, 'unplanned_checkin_ack'))
        _restore()
        return

    if body == "2":
        # Finished now
        end_dt = datetime.now()
        _finish_ongoing_task(db, sender, task_id, end_dt)
        await _send(client, room.room_id,
                    t(lang, 'unplanned_finished',
                      description=description, end=end_dt.strftime('%H:%M')))
        _restore()
        return

    if body == "3":
        # Ask for time
        _set_state(sender, "unplanned_checkin_time",
                   checkin_task_id=task_id,
                   checkin_description=description,
                   checkin_previous_state=prev_state,
                   checkin_previous_data=prev_data)
        await _send(client, room.room_id, t(lang, 'unplanned_checkin_time_prompt'))
        return

    # Try parsing as HH:MM directly
    parsed = _parse_hhmm(body)
    if parsed:
        end_dt = datetime.combine(date.today(), parsed)
        _finish_ongoing_task(db, sender, task_id, end_dt)
        await _send(client, room.room_id,
                    t(lang, 'unplanned_finished',
                      description=description, end=end_dt.strftime('%H:%M')))
        _restore()
        return

    await _send(client, room.room_id, t(lang, 'invalid_option'))


async def _handle_unplanned_checkin_time(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    d = us["data"]
    task_id = d.get("checkin_task_id")
    description = d.get("checkin_description", "?")
    prev_state = d.get("checkin_previous_state")
    prev_data = d.get("checkin_previous_data", {})

    parsed = _parse_hhmm(body)
    if not parsed:
        await _send(client, room.room_id, t(lang, 'unplanned_start_time_invalid'))
        return

    end_dt = datetime.combine(date.today(), parsed)
    _finish_ongoing_task(db, sender, task_id, end_dt)
    await _send(client, room.room_id,
                t(lang, 'unplanned_finished',
                  description=description, end=end_dt.strftime('%H:%M')))

    if prev_state:
        us["state"] = prev_state
        us["data"] = prev_data
    else:
        _go_main_menu(sender)


def _finish_ongoing_task(db, matrix_id, task_id, end_dt):
    """Update task and assignment end times, cancel check-in loop."""
    checkin_task = _ongoing_checkin_tasks.pop(matrix_id, None)
    if checkin_task:
        checkin_task.cancel()

    task_svc = TaskService(db)
    task = task_svc.get_task(task_id)
    if task:
        task_svc.update_task(task_id, end_time=end_dt)

    # Update the soldier's assignment for this task
    assignments = task_svc.get_task_assignments(task_id)
    for asgn in assignments:
        asgn.end_time = end_dt
    db.commit()


# ── Commander menu ───────────────────────────────────────────────────────────

async def _enter_commander_menu(client, room, db, soldier, sender):
    _set_state(sender, "commander_menu")
    us = _get_state(sender)
    us["data"] = {}
    lang = us["lang"]
    await _send(client, room.room_id, t(lang, 'commander_menu'))


async def _handle_commander_menu(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]

    if body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return
    if body == "1":
        _set_state(sender, "commander_readiness", current_date=date.today().isoformat())
        await _show_commander_readiness(client, room, db, soldier, sender)
    elif body == "2":
        _set_state(sender, "commander_stats", weighted=True)
        await _show_commander_stats(client, room, db, soldier, sender)
    elif body == "3":
        _set_state(sender, "commander_create_name")
        us["data"] = {}
        await _send(client, room.room_id, t(lang, 'commander_create_name'))
    elif body == "4":
        await _enter_template_flow(client, room, db, soldier, sender)
    elif body == "5":
        _set_state(sender, "commander_reconcile")
        await _send(client, room.room_id, t(lang, 'commander_reconcile_warning'))
    else:
        await _send(client, room.room_id, t(lang, 'invalid_option'))


# ── Commander readiness ──────────────────────────────────────────────────────

async def _show_commander_readiness(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    current_date = date.fromisoformat(us["data"]["current_date"])
    date_str = current_date.strftime("%d/%m/%Y")

    readiness = get_day_readiness(db, current_date)
    total_soldiers = len(SoldierService(db).list_active_soldiers())
    present_count = readiness["present"]

    lines = [t(lang, 'commander_readiness_header', date=date_str)]
    lines.append(t(lang, 'commander_readiness_present',
                   present=present_count, total=total_soldiers))

    # Role coverage
    reqs = readiness.get("requirements", {})
    req_roles = reqs.get("required_roles", {})
    if req_roles:
        # Get actual role counts from present soldiers
        day_start = datetime.combine(current_date, time.min)
        day_end = datetime.combine(current_date, time(23, 59, 59))
        present_intervals = SoldierService(db).get_all_presence_intervals(
            day_start, day_end, status="PRESENT")
        present_ids = {iv.soldier_id for iv in present_intervals}
        present_soldiers = [
            s for s in SoldierService(db).list_active_soldiers()
            if s.id in present_ids
        ]
        role_counts: dict[str, int] = {}
        for s in present_soldiers:
            for r in (s.role or []):
                role_counts[r] = role_counts.get(r, 0) + 1
        lines.append("  Roles:")
        for role_name, needed in req_roles.items():
            have = role_counts.get(role_name, 0)
            if have >= needed:
                lines.append(t(lang, 'commander_readiness_role_ok',
                               role=role_name, have=have, need=needed))
            else:
                lines.append(t(lang, 'commander_readiness_role_warn',
                               role=role_name, have=have, need=needed))

    status = readiness["status"]
    if status in ("ok", "surplus", "empty"):
        lines.append(t(lang, 'commander_readiness_status_ok'))
    else:
        lines.append(t(lang, 'commander_readiness_status_warn'))

    lines.append(t(lang, 'commander_readiness_nav'))
    await _send(client, room.room_id, "\n".join(lines))


async def _handle_commander_readiness(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    current_date = date.fromisoformat(us["data"]["current_date"])

    if body == "1":
        # Show soldiers
        _set_state(sender, "commander_readiness_soldiers",
                   current_date=current_date.isoformat())
        await _show_commander_soldiers(client, room, db, soldier, sender)
        return
    if body == "2":
        us["data"]["current_date"] = (current_date - timedelta(days=1)).isoformat()
    elif body == "3":
        us["data"]["current_date"] = (current_date + timedelta(days=1)).isoformat()
    elif body == "4":
        us["data"]["current_date"] = date.today().isoformat()
    elif body == "0":
        await _enter_commander_menu(client, room, db, soldier, sender)
        return
    else:
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return
    await _show_commander_readiness(client, room, db, soldier, sender)


# ── Commander readiness soldiers ─────────────────────────────────────────────

async def _show_commander_soldiers(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    current_date = date.fromisoformat(us["data"]["current_date"])
    date_str = current_date.strftime("%d/%m/%Y")

    day_start = datetime.combine(current_date, time.min)
    day_end = datetime.combine(current_date, time(23, 59, 59))

    all_soldiers = SoldierService(db).list_active_soldiers()
    soldier_svc = SoldierService(db)

    present_full = []
    partial = []
    absent = []

    for s in all_soldiers:
        intervals = soldier_svc.get_presence_intervals(s.id, day_start, day_end)
        present_ivs = [iv for iv in intervals if iv.status == "PRESENT"]
        absent_ivs = [iv for iv in intervals if iv.status == "ABSENT"]

        if not intervals:
            absent.append((s, None))
        elif present_ivs and not absent_ivs:
            # Check if fully covering the day
            covers_start = any(iv.start_time <= day_start for iv in present_ivs)
            covers_end = any(iv.end_time >= day_end for iv in present_ivs)
            if covers_start and covers_end:
                present_full.append(s)
            else:
                # Partial — find arrival/departure
                earliest_present = min(iv.start_time for iv in present_ivs)
                latest_present = max(iv.end_time for iv in present_ivs)
                if earliest_present > day_start:
                    partial.append((s, "arrives", earliest_present.strftime('%H:%M')))
                elif latest_present < day_end:
                    partial.append((s, "departs", latest_present.strftime('%H:%M')))
                else:
                    partial.append((s, None, None))
        elif present_ivs and absent_ivs:
            # Mixed — partial
            earliest_present = min(iv.start_time for iv in present_ivs)
            latest_present = max(iv.end_time for iv in present_ivs)
            if earliest_present > day_start:
                partial.append((s, "arrives", earliest_present.strftime('%H:%M')))
            elif latest_present < day_end:
                partial.append((s, "departs", latest_present.strftime('%H:%M')))
            else:
                partial.append((s, None, None))
        else:
            absent.append((s, None))

    lines = [t(lang, 'commander_soldiers_header', date=date_str)]

    if present_full:
        lines.append(t(lang, 'commander_soldiers_present', count=len(present_full)))
        names = ", ".join(display_name(s) for s in present_full)
        lines.append(f"    {names}")
        lines.append("")

    if partial:
        lines.append(t(lang, 'commander_soldiers_partial', count=len(partial)))
        for entry in partial:
            s = entry[0]
            if len(entry) == 3 and entry[1] == "arrives":
                lines.append(t(lang, 'commander_soldiers_partial_arrives',
                               name=display_name(s), time=entry[2]))
            elif len(entry) == 3 and entry[1] == "departs":
                lines.append(t(lang, 'commander_soldiers_partial_departs',
                               name=display_name(s), time=entry[2]))
            else:
                lines.append(t(lang, 'commander_soldiers_partial_plain',
                               name=display_name(s)))
        lines.append("")

    if absent:
        lines.append(t(lang, 'commander_soldiers_absent', count=len(absent)))
        names = ", ".join(display_name(entry[0]) for entry in absent)
        lines.append(f"    {names}")

    lines.append(t(lang, 'commander_soldiers_nav'))
    await _send(client, room.room_id, "\n".join(lines))


async def _handle_commander_readiness_soldiers(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    current_date = date.fromisoformat(us["data"]["current_date"])

    if body == "2":
        us["data"]["current_date"] = (current_date - timedelta(days=1)).isoformat()
    elif body == "3":
        us["data"]["current_date"] = (current_date + timedelta(days=1)).isoformat()
    elif body == "4":
        us["data"]["current_date"] = date.today().isoformat()
    elif body == "0":
        _set_state(sender, "commander_readiness",
                   current_date=us["data"]["current_date"])
        await _show_commander_readiness(client, room, db, soldier, sender)
        return
    else:
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return
    await _show_commander_soldiers(client, room, db, soldier, sender)


# ── Commander stats ──────────────────────────────────────────────────────────

async def _show_commander_stats(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    weighted = us["data"].get("weighted", True)

    config = ConfigService(db).get_config()
    ns = config.night_start_hour if config else 23
    ne = config.night_end_hour if config else 7

    period_start = config.reserve_period_start if config else None
    period_end = config.reserve_period_end if config else None

    sched_svc = ScheduleService(db)
    all_asgn = sched_svc.get_all_active_assignments(start=period_start, end=period_end)

    active_soldiers = SoldierService(db).list_active_soldiers()
    active_ids = [s.id for s in active_soldiers]
    n_active = len(active_ids)

    per_soldier_total: dict[int, float] = defaultdict(float)
    for a in all_asgn:
        dh, nh = _split_day_night_hours(a, ns, ne)
        per_soldier_total[a.soldier_id] += dh + nh

    if weighted and period_start and period_end:
        from src.domain.presence_calc import compute_domain_fractions
        combined_frac_sum: dict[int, float] = {sid: 0.0 for sid in active_ids}
        cur = period_start.date() if isinstance(period_start, datetime) else period_start
        end_d = period_end.date() if isinstance(period_end, datetime) else period_end
        while cur < end_d:
            df, nf = compute_domain_fractions(db, active_ids, cur, ns, ne)
            for sid in active_ids:
                combined_frac_sum[sid] += df.get(sid, 0.0) + nf.get(sid, 0.0)
            cur += timedelta(days=1)

        rates = {}
        for sid in active_ids:
            f = combined_frac_sum.get(sid, 0.0)
            rates[sid] = per_soldier_total.get(sid, 0.0) / f if f > 0.001 else 0.0

        total_frac = sum(combined_frac_sum.get(sid, 0.0) for sid in active_ids)
        total_hours = sum(per_soldier_total.get(sid, 0.0) for sid in active_ids)
        avg = total_hours / total_frac if total_frac > 0.001 else 0.0

        mode_str = t(lang, 'stats_mode_weighted')
        toggle_key = 'commander_stats_toggle_absolute'
        per_soldier_display = rates
    else:
        avg = sum(per_soldier_total.get(sid, 0.0) for sid in active_ids) / n_active if n_active else 0
        mode_str = t(lang, 'stats_mode_absolute')
        toggle_key = 'commander_stats_toggle_weighted'
        per_soldier_display = {sid: per_soldier_total.get(sid, 0.0) for sid in active_ids}

    # Find most/least loaded
    sid_to_name = {s.id: display_name(s) for s in active_soldiers}
    if per_soldier_display:
        most_id = max(per_soldier_display, key=per_soldier_display.get)
        least_id = min(per_soldier_display, key=per_soldier_display.get)
    else:
        most_id = least_id = None

    # Spread
    if per_soldier_display:
        vals = list(per_soldier_display.values())
        spread = (max(vals) - min(vals)) / 2 if vals else 0
    else:
        spread = 0

    lines = [
        t(lang, 'commander_stats_header', mode=mode_str),
        t(lang, 'commander_stats_avg', avg=f"{avg:.1f}"),
    ]
    if most_id is not None:
        lines.append(t(lang, 'commander_stats_most',
                       name=sid_to_name.get(most_id, "?"),
                       hours=f"{per_soldier_display[most_id]:.1f}"))
        lines.append(t(lang, 'commander_stats_least',
                       name=sid_to_name.get(least_id, "?"),
                       hours=f"{per_soldier_display[least_id]:.1f}"))
    lines.append(t(lang, 'commander_stats_spread', spread=f"{spread:.1f}"))
    lines.append("")
    lines.append(t(lang, toggle_key))
    lines.append(t(lang, 'nav_back'))
    await _send(client, room.room_id, "\n".join(lines))


async def _handle_commander_stats(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    if body == "1":
        us["data"]["weighted"] = not us["data"].get("weighted", True)
        await _show_commander_stats(client, room, db, soldier, sender)
    elif body == "0":
        await _enter_commander_menu(client, room, db, soldier, sender)
    else:
        lang = us["lang"]
        await _send(client, room.room_id, t(lang, 'invalid_option'))


# ── Commander create task ────────────────────────────────────────────────────

async def _handle_commander_create_name(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        await _enter_commander_menu(client, room, db, soldier, sender)
        return
    us["data"]["task_name"] = body.strip()
    _set_state(sender, "commander_create_start")
    await _send(client, room.room_id, t(lang, 'commander_create_start'))


async def _handle_commander_create_start(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        await _enter_commander_menu(client, room, db, soldier, sender)
        return
    parsed = _parse_ddmm_hhmm(body)
    if not parsed:
        await _send(client, room.room_id, t(lang, 'commander_create_datetime_invalid'))
        return
    us["data"]["task_start"] = parsed.isoformat()
    _set_state(sender, "commander_create_end")
    await _send(client, room.room_id, t(lang, 'commander_create_end'))


async def _handle_commander_create_end(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        await _enter_commander_menu(client, room, db, soldier, sender)
        return
    parsed = _parse_ddmm_hhmm(body)
    if not parsed:
        await _send(client, room.room_id, t(lang, 'commander_create_datetime_invalid'))
        return
    start_dt = datetime.fromisoformat(us["data"]["task_start"])
    if parsed <= start_dt:
        await _send(client, room.room_id, t(lang, 'commander_create_datetime_invalid'))
        return
    us["data"]["task_end"] = parsed.isoformat()
    _set_state(sender, "commander_create_count")
    await _send(client, room.room_id, t(lang, 'commander_create_count'))


async def _handle_commander_create_count(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        await _enter_commander_menu(client, room, db, soldier, sender)
        return
    try:
        count = int(body)
    except ValueError:
        await _send(client, room.room_id, t(lang, 'commander_create_count_invalid'))
        return
    if count < 1:
        await _send(client, room.room_id, t(lang, 'commander_create_count_invalid'))
        return
    us["data"]["task_count"] = count
    _set_state(sender, "commander_create_difficulty")
    await _send(client, room.room_id, t(lang, 'commander_create_difficulty'))


async def _handle_commander_create_difficulty(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        await _enter_commander_menu(client, room, db, soldier, sender)
        return
    try:
        diff = int(body)
    except ValueError:
        diff = 3
    diff = max(1, min(5, diff))
    us["data"]["task_difficulty"] = diff
    _set_state(sender, "commander_create_fractionable")
    await _send(client, room.room_id, t(lang, 'commander_create_fractionable'))


async def _handle_commander_create_fractionable(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "1":
        us["data"]["task_fractionable"] = True
    elif body == "2":
        us["data"]["task_fractionable"] = False
    else:
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return
    # Ask about roles — reuse the same pattern as unplanned
    us["data"]["selected_roles"] = {}
    _set_state(sender, "commander_create_roles")
    await _send(client, room.room_id, t(lang, 'unplanned_roles_prompt'))


async def _handle_commander_create_roles(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        await _enter_commander_menu(client, room, db, soldier, sender)
        return
    if body == "1":
        us["data"]["selected_roles"] = {}
        await _show_commander_create_confirm(client, room, db, soldier, sender)
    elif body == "2":
        us["data"]["selected_roles"] = {}
        _set_state(sender, "commander_create_role_select")
        has_roles = await _show_role_select(client, room, db, sender, {})
        if not has_roles:
            await _show_commander_create_confirm(client, room, db, soldier, sender)
    else:
        await _send(client, room.room_id, t(lang, 'invalid_option'))


async def _handle_commander_create_role_select(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    selected_roles = us["data"].get("selected_roles", {})

    if body == "0":
        await _show_commander_create_confirm(client, room, db, soldier, sender)
        return

    available = us["data"].get("available_roles", [])
    try:
        idx = int(body) - 1
    except ValueError:
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return
    if idx < 0 or idx >= len(available):
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    role_name = available[idx]
    us["data"]["_pending_role"] = role_name
    _set_state(sender, "commander_create_role_quantity")
    await _send(client, room.room_id, t(lang, 'unplanned_role_quantity', role=role_name))


async def _handle_commander_create_role_quantity(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        _set_state(sender, "commander_create_role_select")
        await _show_role_select(client, room, db, sender, us["data"].get("selected_roles", {}))
        return
    try:
        qty = int(body)
    except ValueError:
        qty = 1
    if qty < 1:
        qty = 1
    role_name = us["data"].pop("_pending_role", "?")
    us["data"].setdefault("selected_roles", {})[role_name] = qty
    _set_state(sender, "commander_create_role_select")
    await _show_role_select(client, room, db, sender, us["data"]["selected_roles"])


async def _show_commander_create_confirm(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    d = us["data"]
    start_dt = datetime.fromisoformat(d["task_start"])
    end_dt = datetime.fromisoformat(d["task_end"])
    roles = d.get("selected_roles", {})
    roles_str = ", ".join(f"{n} \u00d7{q}" for n, q in roles.items()) if roles else "Any"
    frac_str = "Yes" if d.get("task_fractionable", True) else "No"
    _set_state(sender, "commander_create_confirm")
    await _send(client, room.room_id, t(lang, 'commander_create_confirm',
        name=d["task_name"],
        start=start_dt.strftime('%d/%m %H:%M'),
        end=end_dt.strftime('%d/%m %H:%M'),
        count=d.get("task_count", 1),
        difficulty=d.get("task_difficulty", 3),
        fractionable=frac_str,
        roles=roles_str))


async def _handle_commander_create_confirm(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        await _enter_commander_menu(client, room, db, soldier, sender)
        return
    if body != "1":
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    d = us["data"]
    start_dt = datetime.fromisoformat(d["task_start"])
    end_dt = datetime.fromisoformat(d["task_end"])
    roles = d.get("selected_roles", {})
    roles_list = roles if roles else []

    task_svc = TaskService(db)
    try:
        task_svc.create_task(
            real_title=d["task_name"],
            start_time=start_dt,
            end_time=end_dt,
            is_fractionable=d.get("task_fractionable", True),
            required_count=d.get("task_count", 1),
            required_roles_list=roles_list,
            hardness=d.get("task_difficulty", 3),
        )
    except ValueError:
        # Fallback if coverage check fails
        new_task = Task(
            real_title=d["task_name"],
            start_time=start_dt,
            end_time=end_dt,
            is_fractionable=d.get("task_fractionable", True),
            required_count=d.get("task_count", 1),
            required_roles_list=roles_list if roles_list else [],
            base_weight=1.0,
            hardness=d.get("task_difficulty", 3),
            is_active=True,
            coverage_status='UNCOVERED',
        )
        task_svc.save_task(new_task)

    _set_state(sender, "commander_create_done")
    await _send(client, room.room_id, t(lang, 'commander_create_done'))
    await _send(client, room.room_id, t(lang, 'commander_create_done_options'))


async def _handle_commander_create_done(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "1":
        # Run reconcile
        await _send(client, room.room_id, t(lang, 'commander_reconcile_running'))
        await _do_reconcile(client, room, db, soldier, sender)
    else:
        await _enter_commander_menu(client, room, db, soldier, sender)


# ── Commander create from template ────────────────────────────────────────────

async def _enter_template_flow(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    tpl_svc = TemplateService(db)
    templates = tpl_svc.list_templates()
    if not templates:
        await _send(client, room.room_id, t(lang, 'template_none_saved'))
        await _enter_commander_menu(client, room, db, soldier, sender)
        return

    lines = [t(lang, 'template_list_header')]
    for i, tpl in enumerate(templates, 1):
        time_str = f"{tpl.start_time_of_day}–{tpl.end_time_of_day}"
        lines.append(f"  {i}. {tpl.name} ({time_str}, {tpl.required_count} soldiers)")
    lines.append("\n  0. Back")
    us["data"]["_template_ids"] = [tpl.id for tpl in templates]
    _set_state(sender, "commander_template_select")
    await _send(client, room.room_id, "\n".join(lines))


async def _handle_commander_template_select(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        await _enter_commander_menu(client, room, db, soldier, sender)
        return

    template_ids = us["data"].get("_template_ids", [])
    try:
        idx = int(body) - 1
    except ValueError:
        await _send(client, room.room_id, t(lang, 'template_invalid_choice'))
        return
    if idx < 0 or idx >= len(template_ids):
        await _send(client, room.room_id, t(lang, 'template_invalid_choice'))
        return

    tpl_svc = TemplateService(db)
    tpl = tpl_svc.get_template(template_ids[idx])
    if not tpl:
        await _send(client, room.room_id, t(lang, 'template_invalid_choice'))
        return

    # Store template data
    roles = tpl.required_roles_list or {}
    if isinstance(roles, list):
        roles = {r: 1 for r in roles}
    roles_str = ", ".join(f"{n} ×{q}" for n, q in roles.items()) if roles else "Any"
    frac_str = "Yes" if tpl.is_fractionable else "No"

    us["data"]["tpl_id"] = tpl.id
    us["data"]["tpl_name"] = tpl.name
    us["data"]["tpl_start_tod"] = tpl.start_time_of_day
    us["data"]["tpl_end_tod"] = tpl.end_time_of_day
    us["data"]["tpl_crosses"] = tpl.crosses_midnight
    us["data"]["tpl_count"] = tpl.required_count
    us["data"]["tpl_difficulty"] = tpl.hardness
    us["data"]["tpl_fractionable"] = tpl.is_fractionable
    us["data"]["tpl_roles"] = roles
    us["data"]["tpl_roles_str"] = roles_str

    # Show summary + ask for date
    summary = t(lang, 'template_summary',
                name=tpl.name,
                time=f"{tpl.start_time_of_day}–{tpl.end_time_of_day}",
                count=tpl.required_count,
                difficulty=tpl.hardness,
                roles=roles_str,
                fractionable=frac_str)
    await _send(client, room.room_id, summary)
    _set_state(sender, "commander_template_date")
    await _send(client, room.room_id, t(lang, 'template_enter_date'))


async def _handle_commander_template_date(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        await _enter_commander_menu(client, room, db, soldier, sender)
        return

    d = us["data"]
    body_lower = body.strip().lower()

    # Parse date
    if body_lower == "today":
        start_date = date.today()
    elif body_lower == "tomorrow":
        start_date = date.today() + timedelta(days=1)
    else:
        parts = body.strip().split('/')
        if len(parts) != 2:
            await _send(client, room.room_id, t(lang, 'template_invalid_choice'))
            return
        try:
            day_n, month_n = int(parts[0]), int(parts[1])
            start_date = date(datetime.now().year, month_n, day_n)
        except (ValueError, IndexError):
            await _send(client, room.room_id, t(lang, 'template_invalid_choice'))
            return

    # Build datetimes from template time-of-day + user date
    sh, sm = map(int, d["tpl_start_tod"].split(':'))
    eh, em = map(int, d["tpl_end_tod"].split(':'))
    start_dt = datetime.combine(start_date, time(sh, sm))
    if d.get("tpl_crosses"):
        end_date = start_date + timedelta(days=1)
    else:
        end_date = start_date
    end_dt = datetime.combine(end_date, time(eh, em))

    d["task_start"] = start_dt.isoformat()
    d["task_end"] = end_dt.isoformat()

    # Show confirmation
    roles_str = d.get("tpl_roles_str", "Any")
    frac_str = "Yes" if d.get("tpl_fractionable", True) else "No"
    confirm_msg = t(lang, 'template_confirm',
                    name=d["tpl_name"],
                    start=start_dt.strftime('%d/%m %H:%M'),
                    end=end_dt.strftime('%d/%m %H:%M'),
                    count=d.get("tpl_count", 1),
                    difficulty=d.get("tpl_difficulty", 3),
                    roles=roles_str,
                    fractionable=frac_str)
    _set_state(sender, "commander_template_confirm")
    await _send(client, room.room_id, confirm_msg)


async def _handle_commander_template_confirm(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        await _enter_commander_menu(client, room, db, soldier, sender)
        return
    if body != "1":
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    d = us["data"]
    start_dt = datetime.fromisoformat(d["task_start"])
    end_dt = datetime.fromisoformat(d["task_end"])
    roles = d.get("tpl_roles", {})
    roles_list = roles if roles else []

    task_svc = TaskService(db)
    try:
        task_svc.create_task(
            real_title=d["tpl_name"],
            start_time=start_dt,
            end_time=end_dt,
            is_fractionable=d.get("tpl_fractionable", True),
            required_count=d.get("tpl_count", 1),
            required_roles_list=roles_list,
            hardness=d.get("tpl_difficulty", 3),
        )
    except ValueError:
        new_task = Task(
            real_title=d["tpl_name"],
            start_time=start_dt,
            end_time=end_dt,
            is_fractionable=d.get("tpl_fractionable", True),
            required_count=d.get("tpl_count", 1),
            required_roles_list=roles_list if roles_list else [],
            base_weight=1.0,
            hardness=d.get("tpl_difficulty", 3),
            is_active=True,
            coverage_status='UNCOVERED',
        )
        task_svc.save_task(new_task)

    await _send(client, room.room_id, t(lang, 'template_created', name=d["tpl_name"]))
    await _enter_commander_menu(client, room, db, soldier, sender)


# ── Commander reconcile ──────────────────────────────────────────────────────

async def _handle_commander_reconcile(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]
    if body == "0":
        await _enter_commander_menu(client, room, db, soldier, sender)
        return
    if body != "1":
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    await _send(client, room.room_id, t(lang, 'commander_reconcile_running'))
    await _do_reconcile(client, room, db, soldier, sender)


async def _do_reconcile(client, room, db, soldier, sender):
    """Run reconcile and show results. Sends post-reconcile notifications."""
    lang = _get_state(sender)["lang"]
    try:
        sched_svc = ScheduleService(db)

        # Snapshot assignments before reconcile for change detection
        today = date.today()
        tomorrow = today + timedelta(days=1)
        pre_assignments: dict[int, set[str]] = {}
        for day in [today, tomorrow]:
            for a in get_day_schedule(db, day):
                pre_assignments.setdefault(a.soldier_id, set()).add(
                    f"{a.task_id}:{a.start_time.isoformat()}:{a.end_time.isoformat()}"
                )

        sched_svc.reconcile()

        task_svc = TaskService(db)
        all_tasks = task_svc.get_active_future_tasks()
        total = len(all_tasks)
        uncovered = [tk for tk in all_tasks if (tk.coverage_status or 'OK') != 'OK']
        covered = total - len(uncovered)

        result = t(lang, 'commander_reconcile_done', covered=covered, total=total)
        if uncovered:
            uncov_str = ", ".join(
                f"{task_display(tk)} {tk.start_time.strftime('%H:%M')}-{tk.end_time.strftime('%H:%M')}"
                for tk in uncovered
            )
            result += t(lang, 'commander_reconcile_uncovered', tasks=uncov_str)

        await _send(client, room.room_id, result)

        # Post-reconcile: notify affected soldiers of changed assignments
        post_assignments: dict[int, set[str]] = {}
        for day in [today, tomorrow]:
            for a in get_day_schedule(db, day):
                post_assignments.setdefault(a.soldier_id, set()).add(
                    f"{a.task_id}:{a.start_time.isoformat()}:{a.end_time.isoformat()}"
                )

        # Find soldiers whose assignments changed
        all_soldier_ids = set(pre_assignments.keys()) | set(post_assignments.keys())
        soldier_svc = SoldierService(db)
        for sid in all_soldier_ids:
            pre = pre_assignments.get(sid, set())
            post = post_assignments.get(sid, set())
            if pre != post:
                s = soldier_svc.get_soldier(sid)
                if s and s.matrix_id and s.matrix_id != sender:
                    s_lang = s.preferred_language or 'en'
                    # Build personal schedule update
                    lines = [t(s_lang, 'schedule_updated')]
                    for day in [today, tomorrow]:
                        day_asgn = [a for a in get_day_schedule(db, day) if a.soldier_id == sid]
                        for a in day_asgn:
                            lines.append(
                                f"  {task_display(a.task)} "
                                f"{a.start_time.strftime('%d/%m %H:%M')}-{a.end_time.strftime('%H:%M')}"
                            )
                    if len(lines) == 1:
                        lines.append(t(s_lang, 'schedule_cleared'))
                    await _send_to_user(client, s.matrix_id, "\n".join(lines))

        # UNCOVERED alerts to all privileged users (always-on, not category-gated)
        if uncovered:
            uncov_alert = t('en', 'reconcile_uncovered_alert', tasks=uncov_str)
            config = ConfigService(db).get_config()
            chain = (config.command_chain or []) if config else []
            for s in soldier_svc.list_notifiable_soldiers():
                is_priv = s.id in chain or 'Sargent' in (s.role or [])
                if is_priv and s.matrix_id != sender:
                    await _send_to_user(client, s.matrix_id, uncov_alert)

    except Exception:
        logger.exception("Reconcile failed")
        await _send(client, room.room_id, t(lang, 'error'))

    await _enter_commander_menu(client, room, db, soldier, sender)


# ── Notification settings ──────────────────────────────────────────────────────

async def _enter_notification_settings(client, room, db, soldier, sender):
    _set_state(sender, "notification_settings")
    await _show_notification_settings(client, room, db, soldier, sender)


async def _show_notification_settings(client, room, db, soldier, sender):
    us = _get_state(sender)
    lang = us["lang"]
    prefs = _get_notification_prefs(soldier, db)

    on = t(lang, 'notif_on')
    off = t(lang, 'notif_off')
    reports_status = on if prefs.get('soldier_reports') else off
    gear_status = on if prefs.get('gear_changes') else off

    await _send(client, room.room_id, t(lang, 'notification_settings',
        reports=reports_status, gear=gear_status))


async def _handle_notification_settings(client, room, db, soldier, sender, body):
    us = _get_state(sender)
    lang = us["lang"]

    if body == "0":
        _go_main_menu(sender)
        await _show_main_menu(client, room, db, soldier, sender)
        return

    prefs = _get_notification_prefs(soldier, db)

    if body == "1":
        prefs['soldier_reports'] = not prefs['soldier_reports']
    elif body == "2":
        prefs['gear_changes'] = not prefs['gear_changes']
    else:
        await _send(client, room.room_id, t(lang, 'invalid_option'))
        return

    SoldierService(db).update_soldier(soldier.id, notification_prefs=prefs)
    # Re-read soldier to get updated prefs
    soldier = SoldierService(db).get_soldier(soldier.id)
    await _show_notification_settings(client, room, db, soldier, sender)


# ── State handler dispatch table ──────────────────────────────────────────────

_STATE_HANDLERS = {
    "language_select": _handle_language_select,
    "main_menu": _handle_main_menu,
    "my_schedule": _handle_my_schedule,
    "team_schedule": _handle_team_schedule,
    "tasks_view": _handle_tasks_view,
    "my_stats": _handle_my_stats,
    "change_language": _handle_change_language,
    "swap_pick_assignment": _handle_swap_pick_assignment,
    "swap_pick_soldier": _handle_swap_pick_soldier,
    "swap_waiting": _handle_swap_waiting,
    "swap_respond": _handle_swap_respond,
    "my_gear": _handle_my_gear,
    "my_gear_add": _handle_my_gear_add,
    "my_gear_remove": _handle_my_gear_remove,
    "team_gear": _handle_team_gear,
    "team_gear_add": _handle_team_gear_add,
    "team_gear_remove": _handle_team_gear_remove,
    "report_issue": _handle_report_issue,
    # Unplanned task
    "unplanned_warning": _handle_unplanned_warning,
    "unplanned_describe": _handle_unplanned_describe,
    "unplanned_start_time": _handle_unplanned_start_time,
    "unplanned_end_time": _handle_unplanned_end_time,
    "unplanned_needs_more": _handle_unplanned_needs_more,
    "unplanned_how_many": _handle_unplanned_how_many,
    "unplanned_roles_prompt": _handle_unplanned_roles_prompt,
    "unplanned_role_select": _handle_unplanned_role_select,
    "unplanned_role_quantity": _handle_unplanned_role_quantity,
    "unplanned_confirm": _handle_unplanned_confirm,
    "unplanned_checkin": _handle_unplanned_checkin,
    "unplanned_checkin_time": _handle_unplanned_checkin_time,
    # Commander menu
    "commander_menu": _handle_commander_menu,
    "commander_readiness": _handle_commander_readiness,
    "commander_readiness_soldiers": _handle_commander_readiness_soldiers,
    "commander_stats": _handle_commander_stats,
    "commander_create_name": _handle_commander_create_name,
    "commander_create_start": _handle_commander_create_start,
    "commander_create_end": _handle_commander_create_end,
    "commander_create_count": _handle_commander_create_count,
    "commander_create_difficulty": _handle_commander_create_difficulty,
    "commander_create_fractionable": _handle_commander_create_fractionable,
    "commander_create_roles": _handle_commander_create_roles,
    "commander_create_role_select": _handle_commander_create_role_select,
    "commander_create_role_quantity": _handle_commander_create_role_quantity,
    "commander_create_confirm": _handle_commander_create_confirm,
    "commander_create_done": _handle_commander_create_done,
    "commander_template_select": _handle_commander_template_select,
    "commander_template_date": _handle_commander_template_date,
    "commander_template_confirm": _handle_commander_template_confirm,
    "commander_reconcile": _handle_commander_reconcile,
    # Notification settings
    "notification_settings": _handle_notification_settings,
}


# ═════════════════════════════════════════════════════════════════════════════
# PUSH NOTIFICATIONS ON SCHEDULE CHANGES (background)
# ═════════════════════════════════════════════════════════════════════════════

_last_schedule_snapshot: dict[int, set[str]] = {}
_last_uncovered_tasks: set[int] = set()


async def check_schedule_changes(client: AsyncClient):
    """Periodic task: detect assignment changes and notify affected soldiers.
    Uses 'changed tasks → all soldiers on those tasks' approach.
    Also tracks UNCOVERED tasks and alerts privileged users."""
    global _last_schedule_snapshot, _last_uncovered_tasks
    with bot_session() as db:
        today = date.today()
        tomorrow = today + timedelta(days=1)

        # Build current per-task assignment fingerprints
        current_by_task: dict[int, set[str]] = {}  # task_id -> set of "soldier_id:start:end"
        current_by_soldier: dict[int, list[str]] = {}  # soldier_id -> display lines
        for day in [today, tomorrow]:
            for a in get_day_schedule(db, day):
                tk = a.task
                fp = f"{a.soldier_id}:{a.start_time.isoformat()}:{a.end_time.isoformat()}"
                current_by_task.setdefault(a.task_id, set()).add(fp)
                current_by_soldier.setdefault(a.soldier_id, []).append(
                    f"{task_display(tk)} {a.start_time.strftime('%d/%m %H:%M')}-{a.end_time.strftime('%H:%M')}"
                )

        # Find changed tasks
        all_task_ids = set(current_by_task.keys()) | set(_last_schedule_snapshot.keys())
        changed_task_ids = set()
        for tid in all_task_ids:
            prev = _last_schedule_snapshot.get(tid, set())
            curr = current_by_task.get(tid, set())
            if prev != curr:
                changed_task_ids.add(tid)

        # Collect all soldiers affected by changed tasks
        affected_soldier_ids: set[int] = set()
        for day in [today, tomorrow]:
            for a in get_day_schedule(db, day):
                if a.task_id in changed_task_ids:
                    affected_soldier_ids.add(a.soldier_id)
        # Also soldiers who were on changed tasks before (now removed)
        for tid in changed_task_ids:
            for fp in _last_schedule_snapshot.get(tid, set()):
                sid = int(fp.split(":")[0])
                affected_soldier_ids.add(sid)

        # Notify affected soldiers
        soldier_svc = SoldierService(db)
        for sid in affected_soldier_ids:
            soldier = soldier_svc.get_soldier(sid)
            if soldier and soldier.matrix_id:
                lang = soldier.preferred_language or 'en'
                slots = current_by_soldier.get(sid, [])
                if slots:
                    lines = [t(lang, 'schedule_updated')]
                    for slot in slots:
                        lines.append(f"  - {slot}")
                    await _send_to_user(client, soldier.matrix_id, "\n".join(lines))
                else:
                    await _send_to_user(client, soldier.matrix_id, t(lang, 'schedule_cleared'))

        # UNCOVERED tracking — alert privileged users about new UNCOVERED tasks
        task_svc = TaskService(db)
        current_uncovered = set()
        uncov_tasks = task_svc.get_uncovered_tasks()
        for tk in uncov_tasks:
            current_uncovered.add(tk.id)

        new_uncovered = current_uncovered - _last_uncovered_tasks
        if new_uncovered:
            uncov_names = []
            for tid in new_uncovered:
                tk = task_svc.get_task(tid)
                if tk:
                    uncov_names.append(
                        f"{task_display(tk)} {tk.start_time.strftime('%H:%M')}-{tk.end_time.strftime('%H:%M')}"
                    )
            if uncov_names:
                alert = t('en', 'reconcile_uncovered_alert', tasks=", ".join(uncov_names))
                config = ConfigService(db).get_config()
                chain = (config.command_chain or []) if config else []
                for s in soldier_svc.list_notifiable_soldiers():
                    is_priv = s.id in chain or 'Sargent' in (s.role or [])
                    if is_priv:
                        await _send_to_user(client, s.matrix_id, alert)

        _last_schedule_snapshot = current_by_task
        _last_uncovered_tasks = current_uncovered


# ═════════════════════════════════════════════════════════════════════════════
# BROADCAST HELPERS (online / offline / notify)
# ═════════════════════════════════════════════════════════════════════════════

async def broadcast_to_all(client: AsyncClient, message: str):
    """Send a message to all registered soldiers with a Matrix ID."""
    with bot_session() as db:
        soldiers = SoldierService(db).list_notifiable_soldiers()
        for s in soldiers:
            await _send_to_user(client, s.matrix_id, message)


async def notify_all_schedules(client: AsyncClient):
    """Send personal schedule updates to all soldiers (used by desktop NOTIFY button)."""
    notified = 0
    with bot_session() as db:
        today = date.today()
        soldiers = SoldierService(db).list_notifiable_soldiers()

        for soldier in soldiers:
            lang = soldier.preferred_language or 'en'
            lines = [f"Schedule update for {display_name(soldier)}:\n"]
            for offset, day_label in [(0, "Today"), (1, "Tomorrow")]:
                day = today + timedelta(days=offset)
                assignments = get_day_schedule(db, day)
                my_asgn = [a for a in assignments if a.soldier_id == soldier.id]
                if my_asgn:
                    for a in my_asgn:
                        tk = a.task
                        lines.append(
                            f"  {day_label}: {task_display(tk)} "
                            f"({a.start_time.strftime('%H:%M')}-{a.end_time.strftime('%H:%M')})"
                        )
                        day_label = "        "
                else:
                    lines.append(f"  {day_label}: -- free --")
            if await _send_to_user(client, soldier.matrix_id, "\n".join(lines)):
                notified += 1
    return notified


# ═════════════════════════════════════════════════════════════════════════════
# MATRIX BOT RUNNER — daemon thread with nio sync loop
# ═════════════════════════════════════════════════════════════════════════════

class MatrixBotRunner:
    """Wraps the Matrix bot in a background daemon thread with its own
    asyncio event loop.  Call start() to launch, stop() to tear down."""

    def __init__(self, homeserver: str, user: str, token: str):
        self.homeserver = homeserver
        self.user = user
        self.token = token
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._client: AsyncClient | None = None
        self.running = False
        self._error: str | None = None

    @property
    def client(self) -> AsyncClient | None:
        return self._client

    def start(self):
        if self.running:
            return
        self._error = None
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="MatrixBot",
        )
        self._thread.start()

    def _run(self):
        # On Windows, the default ProactorEventLoop uses IOCP which
        # touches COM objects.  Running that in a background thread while
        # Qt owns the main thread's STA apartment causes
        # RPC_E_WRONG_THREAD (0x8001010d).  SelectorEventLoop avoids COM
        # entirely and works fine for aiohttp / matrix-nio.
        if sys.platform == "win32":
            self._loop = asyncio.SelectorEventLoop()
        else:
            self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        except Exception as exc:
            logger.exception("Bot thread crashed")
            self._error = str(exc)
        finally:
            self.running = False

    async def _async_main(self):
        config = AsyncClientConfig(
            encryption_enabled=True,
            store_sync_tokens=True,
        )
        client = AsyncClient(
            self.homeserver,
            self.user,
            config=config,
            store_path=NIO_STORE_PATH,
        )
        self._client = client

        # Login with access token
        client.access_token = self.token
        client.user_id = self.user
        client.device_id = "KAVBOT"

        # Record boot time so we can ignore old messages
        client._boot_ts = int(_time.time() * 1000)

        # Auto-accept room invites
        client.add_event_callback(self._on_invite, InviteMemberEvent)

        # Message handler
        client.add_event_callback(
            lambda room, event: on_message(client, room, event),
            RoomMessageText,
        )

        # Trust all device keys on first use (TOFU)
        client.add_to_device_callback(self._auto_trust_keys)

        # Do an initial sync to populate rooms
        logger.info("Matrix bot: initial sync...")
        await client.sync(timeout=10000, full_state=True)

        self.running = True
        logger.info("KavManager Matrix Bot running (embedded)")

        # Broadcast online
        await broadcast_to_all(client, t('en', 'bot_online'))

        # Start periodic schedule check
        schedule_task = asyncio.create_task(self._schedule_check_loop(client))

        # Continuous sync loop — receives messages until stop is requested
        self._stop_event = asyncio.Event()
        sync_task = asyncio.create_task(self._sync_loop(client))
        await self._stop_event.wait()

        # Broadcast offline
        try:
            await broadcast_to_all(client, t('en', 'bot_offline'))
        except Exception:
            pass

        sync_task.cancel()
        schedule_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass
        await client.close()
        self._client = None
        self.running = False

    async def _sync_loop(self, client: AsyncClient):
        """Continuously sync with the homeserver to receive events."""
        while True:
            try:
                await client.sync(timeout=30000)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Sync error, retrying in 5s...")
                await asyncio.sleep(5)

    async def _on_invite(self, room: MatrixRoom, event: InviteMemberEvent):
        """Auto-accept invites directed at the bot."""
        if event.state_key == self._client.user_id:
            await self._client.join(room.room_id)
            logger.info("Joined room %s", room.room_id)

    async def _auto_trust_keys(self, event):
        """TOFU: automatically trust device keys on first encounter."""
        if not self._client:
            return
        for device_list in self._client.device_store.values():
            for device in device_list.values():
                if not device.verified:
                    self._client.verify_device(device)

    async def _schedule_check_loop(self, client: AsyncClient):
        """Run schedule change detection every 120 seconds."""
        while True:
            await asyncio.sleep(120)
            try:
                await check_schedule_changes(client)
            except Exception:
                logger.exception("Schedule check error")

    def send_message_sync(self, matrix_id: str, text: str) -> bool:
        """Thread-safe helper for the desktop app to send a message via the bot."""
        if not self._loop or not self._client or not self.running:
            return False
        future = asyncio.run_coroutine_threadsafe(
            _send_to_user(self._client, matrix_id, text),
            self._loop,
        )
        try:
            return future.result(timeout=15)
        except Exception:
            return False

    def notify_all_sync(self) -> int:
        """Thread-safe: send schedule notifications to all soldiers. Returns count."""
        if not self._loop or not self._client or not self.running:
            return 0
        future = asyncio.run_coroutine_threadsafe(
            notify_all_schedules(self._client),
            self._loop,
        )
        try:
            return future.result(timeout=60)
        except Exception:
            return 0

    def stop(self):
        if not self._loop or not self._stop_event:
            self.running = False
            return
        self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=15)
        self.running = False


# Keep backward-compatible alias
BotRunner = MatrixBotRunner


# ═════════════════════════════════════════════════════════════════════════════
# STANDALONE ENTRY POINT (python -m src.api.bot)
# ═════════════════════════════════════════════════════════════════════════════

def main():
    init_db()

    db = SessionLocal()
    config = ConfigService(db).get_config()
    db.close()

    if not config or not config.matrix_homeserver_url or not config.matrix_bot_token:
        logger.error(
            "No Matrix bot configured. Set it in the desktop app: "
            "Settings > MATRIX CHAT"
        )
        sys.exit(1)

    runner = MatrixBotRunner(
        config.matrix_homeserver_url,
        config.matrix_bot_user,
        config.matrix_bot_token,
    )
    runner.start()

    logger.info("KavManager Matrix Bot starting (standalone)...")
    try:
        while runner.running or (runner._thread and runner._thread.is_alive()):
            _time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        runner.stop()


if __name__ == "__main__":
    main()
