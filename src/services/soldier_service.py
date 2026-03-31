from datetime import datetime, time, timedelta, date as date_type

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.models import (
    DraftInterval, PresenceInterval, Soldier, SoldierRequest, UnitConfig,
)
from src.domain.presence_rules import insert_presence_interval


class SoldierService:
    """
    Application service for soldier presence timeline and service counters.
    """

    def __init__(self, db: Session):
        self.db = db

    # ── Soldier CRUD ─────────────────────────────────────────────────────────

    def get_soldier(self, soldier_id: int) -> Soldier | None:
        return self.db.query(Soldier).filter(Soldier.id == soldier_id).first()

    def get_soldier_by_matrix_id(self, matrix_id: str) -> Soldier | None:
        return self.db.query(Soldier).filter(Soldier.matrix_id == matrix_id).first()

    def list_active_soldiers(self) -> list[Soldier]:
        return (
            self.db.query(Soldier)
            .filter(Soldier.is_active_in_kav == True)
            .order_by(Soldier.name)
            .all()
        )

    def list_all_soldiers(self) -> list[Soldier]:
        return self.db.query(Soldier).order_by(Soldier.name).all()

    def list_notifiable_soldiers(self) -> list[Soldier]:
        """Active soldiers with a non-empty matrix_id."""
        return (
            self.db.query(Soldier)
            .filter(
                Soldier.is_active_in_kav == True,
                Soldier.matrix_id.isnot(None),
                Soldier.matrix_id != "",
            )
            .all()
        )

    def create_soldier(
        self, name: str, phone_number: str | None = None,
        roles: list | None = None, **fields,
    ) -> Soldier:
        s = Soldier(
            name=name,
            phone_number=phone_number,
            role=roles or [],
            total_day_points=0.0,
            total_night_points=0.0,
            active_reserve_days=0,
            present_days_count=0.0,
            **fields,
        )
        self.db.add(s)
        self.db.commit()
        return s

    def update_soldier(self, soldier_id: int, **fields) -> Soldier | None:
        soldier = self.get_soldier(soldier_id)
        if not soldier:
            return None
        for key, value in fields.items():
            setattr(soldier, key, value)
        self.db.commit()
        return soldier

    def soft_delete_soldier(self, soldier_id: int) -> bool:
        soldier = self.get_soldier(soldier_id)
        if not soldier:
            return False
        soldier.is_active_in_kav = False
        self.db.commit()
        return True

    def update_roles(self, soldier_id: int, roles: list) -> bool:
        """Overwrite a soldier's role list with validation against the Role table."""
        from src.services.config_service import ConfigService
        soldier = self.get_soldier(soldier_id)
        if not soldier:
            return False
        known_roles = {r.name for r in ConfigService(self.db).list_roles()}
        unknown = [r for r in roles if r not in known_roles]
        if unknown:
            raise ValueError(
                f"Unknown role(s): {unknown}. "
                f"Create them in the Role table before assigning. "
                f"Known roles: {sorted(known_roles)}"
            )
        soldier.role = roles
        self.db.commit()
        return True

    # ── Arrivals / departures ────────────────────────────────────────────────

    def register_arrival(self, soldier_id: int, arrival_date: date_type, duty_end_date: date_type) -> str:
        """
        Registers a soldier's arrival and creates a PRESENT interval from arrival_date
        at configured arrival time + availability buffer until duty_end_date at 12:00.
        """
        soldier = self.get_soldier(soldier_id)
        if not soldier:
            return "Error: soldier not found."

        config = self.db.query(UnitConfig).first()
        if config and config.default_arrival_time:
            h, m = map(int, config.default_arrival_time.split(":"))
            arrival_t = time(h, m)
        else:
            arrival_t = time(12, 0)

        buffer_minutes = config.availability_buffer_minutes if config else 60
        physical_start = datetime.combine(arrival_date, arrival_t)
        effective_start = physical_start + timedelta(minutes=buffer_minutes)

        end_dt = datetime.combine(duty_end_date, time(12, 0))
        self.insert_presence(soldier_id, effective_start, end_dt, "PRESENT")
        self.recalculate_service_counters(soldier_id)
        return f"Arrival registered for {soldier.name}."

    def register_departure(
        self,
        soldier_id: int,
        departure_date: date_type,
        return_date: date_type,
        departure_time: time = time(12, 0),
    ) -> str:
        """
        Marks a soldier as ABSENT from departure_date at departure_time until
        return_date at 12:00.
        """
        soldier = self.get_soldier(soldier_id)
        if not soldier:
            return "Error: soldier not found."

        start_dt = datetime.combine(departure_date, departure_time)
        end_dt = datetime.combine(return_date, time(12, 0))
        self.insert_presence(soldier_id, start_dt, end_dt, "ABSENT")
        self.recalculate_service_counters(soldier_id)
        return (
            f"Leave registered for {soldier.name}: "
            f"{start_dt.strftime('%Y-%m-%d %H:%M')} → {end_dt.strftime('%Y-%m-%d %H:%M')}."
        )

    # ── Presence timeline ────────────────────────────────────────────────────

    def insert_presence(self, soldier_id: int, new_start: datetime, new_end: datetime, status: str) -> None:
        insert_presence_interval(self.db, soldier_id, new_start, new_end, status)
        self.db.commit()

    def recalculate_service_counters(self, soldier_id: int) -> bool:
        """
        Recomputes present_days_count (float) and active_reserve_days (int)
        from the soldier's current presence intervals.
        """
        soldier = self.get_soldier(soldier_id)
        if not soldier:
            return False

        intervals = self.db.query(PresenceInterval).filter(
            PresenceInterval.soldier_id == soldier_id
        ).all()

        if not intervals:
            soldier.present_days_count = 0.0
            soldier.active_reserve_days = 0
            self.db.commit()
            return True

        total_present_seconds = sum(
            (i.end_time - i.start_time).total_seconds()
            for i in intervals
            if i.status == "PRESENT"
        )
        soldier.present_days_count = round(total_present_seconds / 86400.0, 2)

        first_arrival = min(i.start_time for i in intervals)
        last_departure = max(i.end_time for i in intervals)
        soldier.active_reserve_days = max(1, (last_departure - first_arrival).days)

        self.db.commit()
        return True

    # ── Presence queries ─────────────────────────────────────────────────────

    def get_daily_status_code(self, soldier_id: int, target_date: date_type) -> str:
        """
        Returns the soldier's presence status code for a calendar day:
          'a' — present all day
          'b' — present part of the day (transition)
          'c' — absent all day
          'd' — no record (not active)
        """
        day_start = datetime.combine(target_date, time(0, 0, 0))
        day_end = datetime.combine(target_date, time(23, 59, 59))

        intervals = self.db.query(PresenceInterval).filter(
            PresenceInterval.soldier_id == soldier_id,
            PresenceInterval.start_time <= day_end,
            PresenceInterval.end_time >= day_start,
        ).all()

        if not intervals:
            return 'd'

        if any(i.status == 'PRESENT' and i.start_time <= day_start and i.end_time >= day_end
               for i in intervals):
            return 'a'

        if any(i.status == 'ABSENT' and i.start_time <= day_start and i.end_time >= day_end
               for i in intervals):
            return 'c'

        return 'b'

    def generate_ui_grid(self, start_date: date_type, days: int = 7) -> dict:
        """Generates a matrix for the UI to paint the presence grid."""
        grid = {}
        for soldier in self.db.query(Soldier).all():
            grid[soldier.name] = {
                (start_date + timedelta(days=i)).strftime('%Y-%m-%d'):
                    self.get_daily_status_code(soldier.id, start_date + timedelta(days=i))
                for i in range(days)
            }
        return grid

    def get_presence_intervals(
        self, soldier_id: int, start: datetime, end: datetime,
    ) -> list[PresenceInterval]:
        return (
            self.db.query(PresenceInterval)
            .filter(
                PresenceInterval.soldier_id == soldier_id,
                PresenceInterval.start_time < end,
                PresenceInterval.end_time > start,
            )
            .all()
        )

    def get_presence_intervals_for_status(
        self, soldier_id: int, status: str, start: datetime, end: datetime,
    ) -> list[PresenceInterval]:
        """Get presence intervals for a soldier with a specific status in a time window."""
        return (
            self.db.query(PresenceInterval)
            .filter(
                PresenceInterval.soldier_id == soldier_id,
                PresenceInterval.status == status,
                PresenceInterval.start_time <= end,
                PresenceInterval.end_time > start,
            )
            .all()
        )

    def get_all_presence_intervals(
        self, start: datetime, end: datetime, status: str | None = None,
    ) -> list[PresenceInterval]:
        """Get all presence intervals overlapping a window, optionally filtered by status."""
        q = self.db.query(PresenceInterval).filter(
            PresenceInterval.start_time < end,
            PresenceInterval.end_time > start,
        )
        if status:
            q = q.filter(PresenceInterval.status == status)
        return q.all()

    def get_draft_intervals(
        self, soldier_id: int, start: datetime, end: datetime,
    ) -> list[DraftInterval]:
        return (
            self.db.query(DraftInterval)
            .filter(
                DraftInterval.soldier_id == soldier_id,
                DraftInterval.start_time < end,
                DraftInterval.end_time > start,
            )
            .all()
        )

    # ── Grid bulk queries (replaces raw SQL in grid_tab) ─────────────────────

    def get_month_presence_raw(
        self, month_start: datetime, month_end: datetime,
    ) -> dict[int, list[tuple]]:
        """
        Load presence intervals for a month window via raw SQL to bypass
        ORM cache and always see committed rows.
        Returns {soldier_id: [(soldier_id, start_time, end_time, status), ...]}.
        """
        from src.core.database import engine as db_engine

        ms_str = month_start.strftime("%Y-%m-%d %H:%M:%S")
        me_str = month_end.strftime("%Y-%m-%d %H:%M:%S")
        result_map: dict[int, list[tuple]] = {}

        with db_engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT soldier_id, start_time, end_time, status "
                    "FROM presence_intervals WHERE start_time < :me AND end_time > :ms"
                ),
                {"me": me_str, "ms": ms_str},
            )
            for row in result:
                sid = int(row[0])
                result_map.setdefault(sid, []).append(
                    (sid, row[1], row[2], row[3])
                )

        return result_map

    def get_month_draft_raw(
        self, month_start: datetime, month_end: datetime,
    ) -> dict[int, list[tuple]]:
        """
        Load draft intervals for a month window via raw SQL.
        Returns {soldier_id: [(soldier_id, start_time, end_time, status), ...]}.
        """
        from src.core.database import engine as db_engine

        ms_str = month_start.strftime("%Y-%m-%d %H:%M:%S")
        me_str = month_end.strftime("%Y-%m-%d %H:%M:%S")
        result_map: dict[int, list[tuple]] = {}

        with db_engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT soldier_id, start_time, end_time, status "
                    "FROM draft_intervals WHERE start_time < :me AND end_time > :ms"
                ),
                {"me": me_str, "ms": ms_str},
            )
            for row in result:
                sid = int(row[0])
                result_map.setdefault(sid, []).append(
                    (sid, row[1], row[2], row[3])
                )

        return result_map

    def get_month_export_data(
        self, month_start: datetime, month_end: datetime,
    ) -> tuple[list[Soldier], dict[int, list[PresenceInterval]]]:
        """Get active soldiers and their presence intervals for grid export."""
        soldiers = (
            self.db.query(Soldier)
            .filter(Soldier.is_active_in_kav == True)
            .order_by(Soldier.name)
            .all()
        )
        intervals = self.db.query(PresenceInterval).filter(
            PresenceInterval.start_time < month_end,
            PresenceInterval.end_time > month_start,
        ).all()
        by_soldier: dict[int, list[PresenceInterval]] = {}
        for iv in intervals:
            by_soldier.setdefault(iv.soldier_id, []).append(iv)
        return soldiers, by_soldier

    # ── Soldier requests ─────────────────────────────────────────────────────

    def get_soldier_requests(self, soldier_id: int) -> list[SoldierRequest]:
        return (
            self.db.query(SoldierRequest)
            .filter(SoldierRequest.soldier_id == soldier_id)
            .order_by(SoldierRequest.created_at.desc())
            .all()
        )

    def create_request(
        self, soldier_id: int, request_type: str, description: str,
        status: str = "PENDING",
    ) -> SoldierRequest:
        req = SoldierRequest(
            soldier_id=soldier_id,
            request_type=request_type,
            description=description,
            status=status,
            created_at=datetime.now(),
        )
        self.db.add(req)
        self.db.commit()
        return req

    def get_request(self, request_id: int) -> SoldierRequest | None:
        return self.db.query(SoldierRequest).filter(SoldierRequest.id == request_id).first()

    def update_request(self, request_id: int, **fields) -> SoldierRequest | None:
        req = self.get_request(request_id)
        if not req:
            return None
        for key, value in fields.items():
            setattr(req, key, value)
        self.db.commit()
        return req

    def delete_request(self, request_id: int) -> bool:
        req = self.get_request(request_id)
        if not req:
            return False
        self.db.delete(req)
        self.db.commit()
        return True

    # ── Role qualification helpers (absorbed from UnitManager) ───────────────

    def soldier_qualifies_for_required_role(self, soldier: Soldier, required_role: str) -> bool:
        if required_role == "Soldier":
            return True
        soldier_roles = soldier.role or []
        if required_role in soldier_roles:
            return True
        return any(self.role_inherits_from(r, required_role) for r in soldier_roles)

    def role_inherits_from(self, role_name: str, ancestor_name: str) -> bool:
        from src.services.config_service import ConfigService
        config_svc = ConfigService(self.db)
        role = config_svc.get_role_by_name(role_name)
        if not role:
            return False
        visited: set[int] = set()
        current = role
        while current and current.parent_role_id is not None:
            if current.id in visited:
                break
            visited.add(current.id)
            parent = config_svc.get_role(current.parent_role_id)
            if not parent:
                break
            if parent.name == ancestor_name:
                return True
            current = parent
        return False

    # ── Leave-solver helpers (absorbed from UnitManager) ─────────────────────

    def ranked_candidates(
        self,
        exclude_soldier_id: int,
        period_from: date_type,
        period_to: date_type,
    ) -> list[tuple[Soldier, int]]:
        """
        Returns all active soldiers (except the requesting one), each paired with
        their total planned present-days in [period_from, period_to].
        Sorted ascending — fewest present-days = most available = best candidate.
        """
        soldiers = (
            self.db.query(Soldier)
            .filter(Soldier.is_active_in_kav == True, Soldier.id != exclude_soldier_id)
            .all()
        )
        period_start_dt = datetime.combine(period_from, time(0, 0, 0))
        period_end_dt = datetime.combine(period_to, time(23, 59, 59))

        scored: list[tuple[Soldier, int]] = []
        for s in soldiers:
            intervals = self.db.query(PresenceInterval).filter(
                PresenceInterval.soldier_id == s.id,
                PresenceInterval.status == "PRESENT",
                PresenceInterval.start_time < period_end_dt,
                PresenceInterval.end_time > period_start_dt,
            ).all()
            present_secs = sum(
                (min(i.end_time, period_end_dt) - max(i.start_time, period_start_dt)).total_seconds()
                for i in intervals
            )
            present_days = round(present_secs / 86400.0, 1)
            scored.append((s, present_days))

        scored.sort(key=lambda x: x[1])
        return scored

    def coverable_days(
        self,
        soldier: Soldier,
        days: list[date_type],
    ) -> list[tuple[date_type, str | None]]:
        """
        Returns list of (day, conflict_note|None) for days the soldier could cover.
        """
        result = []
        for d in days:
            day_start = datetime.combine(d, time(0, 0, 0))
            day_end = datetime.combine(d, time(23, 59, 59))

            is_present = self.db.query(PresenceInterval).filter(
                PresenceInterval.soldier_id == soldier.id,
                PresenceInterval.status == "PRESENT",
                PresenceInterval.start_time < day_end,
                PresenceInterval.end_time > day_start,
            ).first() is not None

            if is_present:
                continue

            leave_req = self.db.query(SoldierRequest).filter(
                SoldierRequest.soldier_id == soldier.id,
                SoldierRequest.request_type == "LEAVE_REQUEST",
                SoldierRequest.status == "PENDING",
            ).first()
            conflict_note = (
                f"\u26a0 Pending leave request on {d.strftime('%d %b')}"
                if leave_req else None
            )
            result.append((d, conflict_note))
        return result

    def normalize_presence_window(
        self,
        soldier_id: int,
        day_from: date_type,
        day_to: date_type,
        overrides: dict[date_type, str] | None = None,
        expand_same_state: str | None = None,
    ) -> None:
        """
        Normalises presence intervals for a soldier in [day_from, day_to].
        Absorbed from UnitManager._normalize_presence_window.
        """
        if day_to < day_from:
            return

        overrides = overrides or {}

        def _day_state(d: date_type) -> str:
            ds = datetime.combine(d, time(0, 0, 0))
            de = datetime.combine(d, time(23, 59, 59))
            ivs = self.db.query(PresenceInterval).filter(
                PresenceInterval.soldier_id == soldier_id,
                PresenceInterval.start_time < de,
                PresenceInterval.end_time > ds,
            ).all()
            has_present = any(iv.status == "PRESENT" for iv in ivs)
            has_absent = any(iv.status == "ABSENT" for iv in ivs)
            if has_present:
                return "P"
            if has_absent:
                return "A"
            return "N"

        if expand_same_state in ("P", "A"):
            d = day_from - timedelta(days=1)
            while _day_state(d) == expand_same_state:
                day_from = d
                d -= timedelta(days=1)
            d = day_to + timedelta(days=1)
            while _day_state(d) == expand_same_state:
                day_to = d
                d += timedelta(days=1)

        states: dict[date_type, str] = {}
        d = day_from
        while d <= day_to:
            states[d] = overrides.get(d) or _day_state(d)
            d += timedelta(days=1)

        prev_day = day_from - timedelta(days=1)
        prev_state: str | None = _day_state(prev_day)

        window_start_dt = datetime.combine(day_from, time.min)
        window_end_dt = datetime.combine(day_to + timedelta(days=1), time.min)
        for iv in self.db.query(PresenceInterval).filter(
            PresenceInterval.soldier_id == soldier_id,
            PresenceInterval.start_time < window_end_dt,
            PresenceInterval.end_time > window_start_dt,
        ).all():
            self.db.delete(iv)

        mid = time(12, 0, 0)
        sorted_days = sorted(states.keys())
        for d in sorted_days:
            curr = states[d]
            day_start = datetime.combine(d, time(0, 0, 0))
            day_end = datetime.combine(d, time(23, 59, 59))

            if curr == "N":
                prev_state = curr
                continue

            is_first_absent = prev_state == "P" and curr == "A"
            is_first_present = prev_state == "A" and curr == "P"

            if is_first_absent:
                self.insert_presence(soldier_id, day_start, datetime.combine(d, mid), "PRESENT")
                self.insert_presence(soldier_id, datetime.combine(d, mid), day_end, "ABSENT")
            elif is_first_present:
                self.insert_presence(soldier_id, day_start, datetime.combine(d, mid), "ABSENT")
                self.insert_presence(soldier_id, datetime.combine(d, mid), day_end, "PRESENT")
            else:
                status = "PRESENT" if curr == "P" else "ABSENT"
                self.insert_presence(soldier_id, day_start, day_end, status)

            prev_state = curr

    # ── Draft interval management ──────────────────────────────────────────

    def get_all_draft_intervals(self, soldier_id: int) -> list[DraftInterval]:
        return (
            self.db.query(DraftInterval)
            .filter(DraftInterval.soldier_id == soldier_id)
            .all()
        )

    def set_drafted_range(
        self, soldier_id: int, range_start: datetime, range_end: datetime, drafted: bool,
    ) -> None:
        """Set or clear DRAFTED status for a date range, merging/carving as needed."""
        if drafted:
            intervals = self.get_all_draft_intervals(soldier_id)
            for iv in intervals:
                if iv.status != "DRAFTED":
                    continue
                if iv.end_time <= range_start or iv.start_time >= range_end:
                    continue
                range_start = min(range_start, iv.start_time)
                range_end = max(range_end, iv.end_time)
                self.db.delete(iv)
            self.db.add(
                DraftInterval(
                    soldier_id=soldier_id,
                    start_time=range_start,
                    end_time=range_end,
                    status="DRAFTED",
                )
            )
        else:
            intervals = self.get_all_draft_intervals(soldier_id)
            for iv in intervals:
                if iv.status != "DRAFTED":
                    continue
                if iv.end_time <= range_start or iv.start_time >= range_end:
                    continue
                if iv.start_time >= range_start and iv.end_time <= range_end:
                    self.db.delete(iv)
                elif iv.start_time < range_start < iv.end_time <= range_end:
                    iv.end_time = range_start
                elif range_start <= iv.start_time < range_end < iv.end_time:
                    iv.start_time = range_end
                elif iv.start_time < range_start and iv.end_time > range_end:
                    left = DraftInterval(
                        soldier_id=iv.soldier_id,
                        start_time=iv.start_time,
                        end_time=range_start,
                        status="DRAFTED",
                    )
                    right = DraftInterval(
                        soldier_id=iv.soldier_id,
                        start_time=range_end,
                        end_time=iv.end_time,
                        status="DRAFTED",
                    )
                    self.db.add(left)
                    self.db.add(right)
                    self.db.delete(iv)
        self.db.commit()

    def count_presence_intervals(self, soldier_id: int) -> int:
        """Count total presence intervals for a soldier (raw SQL, bypasses ORM cache)."""
        from src.core.database import engine as db_engine
        with db_engine.connect() as conn:
            n = conn.execute(
                text("SELECT COUNT(*) FROM presence_intervals WHERE soldier_id = :sid"),
                {"sid": soldier_id},
            ).scalar()
        return n or 0

    # ── Commander helpers ────────────────────────────────────────────────────

    def get_commander_display_name(self, config: UnitConfig) -> str:
        """Return commander display name from config."""
        if config.commander_soldier_id is not None:
            cmd = self.get_soldier(config.commander_soldier_id)
            if cmd:
                return cmd.name or f"#{cmd.id}"
        return getattr(config, "commander_codename", None) or "ACTUAL"

    # ── Swap candidates (bot) ────────────────────────────────────────────────

    def get_swap_candidates(
        self, exclude_soldier_id: int,
        start_time: datetime, end_time: datetime,
    ) -> list[Soldier]:
        """Find active soldiers present during the entire assignment window."""
        return (
            self.db.query(Soldier)
            .join(PresenceInterval)
            .filter(
                Soldier.id != exclude_soldier_id,
                Soldier.is_active_in_kav == True,
                PresenceInterval.status == "PRESENT",
                PresenceInterval.start_time <= start_time,
                PresenceInterval.end_time >= end_time,
            )
            .distinct()
            .all()
        )

    # ── Session helpers (allow UI to manage session without importing db) ────

    def expire(self, obj) -> None:
        """Expire a single ORM object so the next access re-reads from DB."""
        self.db.expire(obj)

    def expire_all(self) -> None:
        """Expire all ORM objects in the session."""
        self.db.expire_all()

    def rollback(self) -> None:
        """Roll back the current transaction."""
        self.db.rollback()
