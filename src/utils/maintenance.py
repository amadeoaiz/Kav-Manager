import shutil
import os
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session
from src.core.models import Soldier, Task, TaskAssignment, PresenceInterval, UnitConfig


def resync_soldier_rates(db: Session) -> None:
    """Recompute each soldier's hours-per-present-domain-hour rate for day
    and night, then store the difference from the unit average into
    total_day_points / total_night_points.

    Presence-weighted: the denominator is total domain-specific presence
    hours (how many day-domain / night-domain hours the soldier was
    actually present for), not raw calendar days.

    Formula per soldier:
        rate = total_domain_hours / domain_presence_hours  (or 0 if none)
        stored value = rate - unit_weighted_avg_rate

    Only past assignments (end_time <= now) are counted.
    Night classification uses UnitConfig.night_start_hour / night_end_hour.
    """
    from src.domain.presence_calc import compute_total_domain_presence

    now = datetime.now()
    config = db.query(UnitConfig).first()
    night_start = config.night_start_hour if config else 23
    night_end = config.night_end_hour if config else 7

    soldiers = db.query(Soldier).filter(Soldier.is_active_in_kav == True).all()
    if not soldiers:
        return

    soldier_ids = [s.id for s in soldiers]

    past_assignments = (
        db.query(TaskAssignment)
        .filter(TaskAssignment.end_time <= now)
        .all()
    )

    # Accumulate day/night hours per soldier using 15-min slice walking.
    sol_day_hours: dict[int, float] = {sid: 0.0 for sid in soldier_ids}
    sol_night_hours: dict[int, float] = {sid: 0.0 for sid in soldier_ids}
    step = timedelta(minutes=15)

    for a in past_assignments:
        if a.soldier_id not in sol_day_hours:
            continue
        c = a.start_time
        end = a.end_time
        while c < end:
            sl = min(c + step, end)
            frac_h = (sl - c).total_seconds() / 3600.0
            if night_end <= night_start:
                is_night = c.hour >= night_start or c.hour < night_end
            else:
                is_night = night_start <= c.hour < night_end
            if is_night:
                sol_night_hours[a.soldier_id] += frac_h
            else:
                sol_day_hours[a.soldier_id] += frac_h
            c = sl

    # Domain-specific presence hours per soldier.
    sol_day_pres, sol_night_pres = compute_total_domain_presence(
        db, soldier_ids, night_start, night_end,
    )

    # Compute per-soldier rate (hours / domain presence hours).
    day_rates: dict[int, float] = {}
    night_rates: dict[int, float] = {}
    for sid in soldier_ids:
        dp = sol_day_pres.get(sid, 0.0)
        np_ = sol_night_pres.get(sid, 0.0)
        day_rates[sid] = sol_day_hours[sid] / dp if dp > 0 else 0.0
        night_rates[sid] = sol_night_hours[sid] / np_ if np_ > 0 else 0.0

    # Presence-weighted unit average: Σ hours / Σ presence.
    total_day_hours = sum(sol_day_hours.values())
    total_night_hours = sum(sol_night_hours.values())
    total_day_pres = sum(sol_day_pres.values())
    total_night_pres = sum(sol_night_pres.values())
    avg_day_rate = total_day_hours / total_day_pres if total_day_pres > 0 else 0.0
    avg_night_rate = total_night_hours / total_night_pres if total_night_pres > 0 else 0.0

    # Store difference from average.
    for s in soldiers:
        s.total_day_points = day_rates[s.id] - avg_day_rate
        s.total_night_points = night_rates[s.id] - avg_night_rate

    db.flush()


class MaintenanceManager:
    def __init__(self, db_session, db_path, backup_dir):
        self.db = db_session
        self.db_path = db_path
        self.backup_dir = backup_dir

    def run_full_maintenance(self, tag="scheduled"):
        print(f"[{datetime.now()}] Starting Maintenance...")
        resync_soldier_rates(self.db)
        self._create_timestamped_backup(tag)
        self._cleanup_old_backups(days_to_keep=14)
        print("Maintenance Complete.")

    def _create_timestamped_backup(self, tag):
        if not os.path.exists(self.backup_dir):
            os.makedirs(self.backup_dir)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        filename = f"backup_{timestamp}_{tag}.db"
        dest_path = os.path.join(self.backup_dir, filename)
        shutil.copy2(self.db_path, dest_path)
        print(f"Saved backup: {filename}")

    def _cleanup_old_backups(self, days_to_keep):
        now = datetime.now()
        if not os.path.exists(self.backup_dir):
            return
        for file in os.listdir(self.backup_dir):
            file_path = os.path.join(self.backup_dir, file)
            if os.path.isfile(file_path):
                file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                if now - file_time > timedelta(days=days_to_keep):
                    os.remove(file_path)
                    print(f"Deleted old backup: {file}")

    def get_last_run(self):
        """Returns the timestamp of the last successful maintenance run."""
        sync_file = os.path.join(os.path.dirname(self.db_path), ".last_maint")
        if not os.path.exists(sync_file):
            return None
        try:
            with open(sync_file, "r") as f:
                return datetime.fromisoformat(f.read().strip())
        except Exception:
            return None

    def update_last_run(self):
        """Records the current time as the last successful maintenance run."""
        sync_file = os.path.join(os.path.dirname(self.db_path), ".last_maint")
        with open(sync_file, "w") as f:
            f.write(datetime.now().isoformat())

    def restore_from_backup(self, filename):
        """
        Swaps the live database with a backup.
        Designed to be triggered by the UI restore button.
        """
        source = os.path.join(self.backup_dir, filename)
        if not os.path.exists(source):
            return False, "Backup file not found."

        try:
            safety_copy = self.db_path + ".pre_restore"
            shutil.copy2(self.db_path, safety_copy)

            self.db.close()
            shutil.copy2(source, self.db_path)
            # Remove WAL/SHM files so SQLite doesn't replay stale
            # write-ahead log data on top of the restored backup.
            for suffix in ("-wal", "-shm"):
                aux = self.db_path + suffix
                if os.path.exists(aux):
                    os.remove(aux)

            sync_file = os.path.join(os.path.dirname(self.db_path), ".last_maint")
            if os.path.exists(sync_file):
                os.remove(sync_file)

            return True, "Success! Please restart the app to finalize."
        except Exception as e:
            return False, f"Restore failed: {str(e)}"
