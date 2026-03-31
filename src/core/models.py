from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, JSON
from sqlalchemy.orm import declarative_base, relationship
import datetime

Base = declarative_base()


class UnitConfig(Base):
    """Single-row table that stores all tunable parameters for the unit and engine.
    The Settings tab reads and writes this. The engine reads it on every reconcile.
    """
    __tablename__ = 'unit_config'

    id = Column(Integer, primary_key=True)

    # Unit display name
    unit_codename = Column(String, default='UNIT-1')
    commander_codename = Column(String, default='ACTUAL')  # legacy, kept for DB compat; auth uses commander_soldier_id
    commander_soldier_id = Column(Integer, ForeignKey('soldiers.id'), nullable=True)

    # Presence planning defaults
    default_arrival_time = Column(String, default='12:00')    # "HH:MM"
    default_departure_time = Column(String, default='12:00')  # "HH:MM"
    availability_buffer_minutes = Column(Integer, default=60) # gear-up time after arrival

    # Night window
    night_start_hour = Column(Integer, default=23)
    night_end_hour = Column(Integer, default=7)

    # Engine tuning
    minimum_assignment_minutes = Column(Integer, default=30)
    adjacency_bonus = Column(Float, default=-15.0)
    wake_up_penalty_base = Column(Float, default=50.0)
    wake_up_decay_alpha = Column(Float, default=2.0)

    # Interface
    theme = Column(String, default='dark')   # 'dark' | 'light'

    # Export settings
    google_sheets_id   = Column(String, nullable=True)   # spreadsheet ID or URL
    google_creds_path  = Column(String, nullable=True)   # path to service-account JSON
    default_export_dir = Column(String, nullable=True)   # default directory for PDF/CSV exports

    # Matrix bot settings
    matrix_homeserver_url = Column(String, nullable=True)   # e.g. https://abc-xyz.trycloudflare.com
    matrix_bot_user       = Column(String, nullable=True)   # e.g. @kavbot:kavmanager.local
    matrix_bot_token      = Column(String, nullable=True)   # access token from login

    swap_approval_timeout_minutes = Column(Integer, default=15)

    # Chain of command: ordered list of up to 3 soldier IDs [primary, secondary, tertiary].
    command_chain = Column(JSON, default=list)

    # Reserve period boundaries (nullable — None means auto-detect from data).
    reserve_period_start = Column(DateTime, nullable=True)
    reserve_period_end = Column(DateTime, nullable=True)


class Soldier(Base):
    __tablename__ = 'soldiers'

    id = Column(Integer, primary_key=True)
    name = Column(String)
    codename = Column(String, nullable=True)        # legacy, kept for DB compat; not used in UI or bot
    phone_number = Column(String)
    matrix_id = Column(String, nullable=True)     # e.g. @wolf:kavmanager.local
    role = Column(JSON)                             # ["Driver", "Medic"]
    is_active_in_kav = Column(Boolean, default=True)

    # Fairness ledger
    total_day_points = Column(Float, default=0.0)
    total_night_points = Column(Float, default=0.0)
    active_reserve_days = Column(Integer, default=0)
    present_days_count = Column(Float, default=0.0)

    last_task_end = Column(DateTime, nullable=True, default=None)
    preferred_language = Column(String, default='en')
    notification_prefs = Column(JSON, nullable=True, default=None)

    presence = relationship("PresenceInterval", back_populates="soldier")
    assignments = relationship("TaskAssignment", back_populates="soldier")
    requests = relationship("SoldierRequest", back_populates="soldier", order_by="SoldierRequest.created_at.desc()")
    gear = relationship("GearItem", back_populates="soldier", order_by="GearItem.item_name")


class Role(Base):
    """Registry of all valid roles with parent-child inheritance."""
    __tablename__ = 'roles'

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=True)
    parent_role_id = Column(Integer, ForeignKey('roles.id'), nullable=True)

    parent = relationship("Role", remote_side=[id], backref="children")


class Task(Base):
    __tablename__ = 'tasks'

    id = Column(Integer, primary_key=True)
    real_title = Column(String)
    codename = Column(String, nullable=True)  # legacy, kept for DB compat; not used in UI or bot

    # Logic
    is_fractionable = Column(Boolean, default=True)
    required_count = Column(Integer, default=1)
    required_roles_list = Column(JSON, default=list)
    base_weight = Column(Float, default=1.0)
    # Physical/mental hardship of the task (1 = comfortable, 5 = nasty).
    hardness = Column(Integer, default=3)
    is_active = Column(Boolean, default=True)

    # Timing
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    readiness_minutes = Column(Integer, default=0)

    # Per-task soldier exclusion: JSON list of soldier IDs that may not be assigned.
    excluded_soldier_ids = Column(JSON, default=list)

    # Chain of command: when False (default), the active commander is excluded.
    include_commander = Column(Boolean, default=False)

    # Coverage flag set by reconcile. Final values are only 'OK' or 'UNCOVERED'.
    # 'PARTIAL' may appear transiently during allocation but is always resolved by gap-fill.
    coverage_status = Column(String, default='OK')

    assignments = relationship("TaskAssignment", back_populates="task")


class PresenceInterval(Base):
    """Soldier timeline. Exactly one interval covers any point in time."""
    __tablename__ = 'presence_intervals'

    id = Column(Integer, primary_key=True)
    soldier_id = Column(Integer, ForeignKey('soldiers.id'))
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    status = Column(String)                         # 'PRESENT' | 'ABSENT'

    soldier = relationship("Soldier", back_populates="presence")


class DraftInterval(Base):
    """Draft status timeline. Marks when a soldier is considered in the active reserve pool.

    Presence and draft are separate concepts:
      · Drafted + no presence info  → active but unknown on-base status (grey in grid)
      · Not drafted                 → outside reserve rotation (empty grid cell)
      · Drafted + presence info     → coloured by PRESENT/ABSENT logic
    """
    __tablename__ = 'draft_intervals'

    id = Column(Integer, primary_key=True)
    soldier_id = Column(Integer, ForeignKey('soldiers.id'))
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    status = Column(String)  # 'DRAFTED' (future-proofed for other states)


class TaskAssignment(Base):
    """Records who did (or will do) a task slot."""
    __tablename__ = 'task_assignments'

    id = Column(Integer, primary_key=True)
    soldier_id = Column(Integer, ForeignKey('soldiers.id'))
    task_id = Column(Integer, ForeignKey('tasks.id'))
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    final_weight_applied = Column(Float)
    pending_review = Column(Boolean, default=False)
    is_pinned = Column(Boolean, default=False)

    soldier = relationship("Soldier", back_populates="assignments")
    task = relationship("Task", back_populates="assignments")


class MissionRequirement(Base):
    """Defines readiness requirements for a date range.
    Multiple overlapping rules are merged (union of roles, max of soldier counts).
    The commander edits these via the Home tab Readiness panel."""
    __tablename__ = 'mission_requirements'

    id = Column(Integer, primary_key=True)
    date_from = Column(DateTime, nullable=False)   # inclusive start
    date_to   = Column(DateTime, nullable=False)   # inclusive end
    label     = Column(String, nullable=True)       # e.g. "Exercise OMEGA"
    min_soldiers = Column(Integer, default=1)
    required_roles = Column(JSON, default=list)     # ["Driver", "Medic", ...]
    note      = Column(String, nullable=True)


class TeamGearItem(Base):
    """Unit-level equipment list (rashmatz). Items belonging to the team, not individual soldiers."""
    __tablename__ = 'team_gear_items'

    id            = Column(Integer, primary_key=True)
    item_name     = Column(String, nullable=False)
    quantity      = Column(Integer, default=1)
    serial_number = Column(String, nullable=True)
    notes         = Column(String, nullable=True)


class GearItem(Base):
    """Equipment signed out to a soldier. Tracks item name, quantity, and serial number."""
    __tablename__ = 'gear_items'

    id            = Column(Integer, primary_key=True)
    soldier_id    = Column(Integer, ForeignKey('soldiers.id'), nullable=False)
    item_name     = Column(String, nullable=False)
    quantity      = Column(Integer, default=1)
    serial_number = Column(String, nullable=True)

    soldier = relationship("Soldier", back_populates="gear")


class TaskTemplate(Base):
    """Saved task preset — stores the recurring shape of a task so commanders
    can quickly create tasks from saved templates."""
    __tablename__ = 'task_templates'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    start_time_of_day = Column(String, nullable=False)       # "HH:MM"
    end_time_of_day = Column(String, nullable=False)         # "HH:MM"
    crosses_midnight = Column(Boolean, default=False)
    is_fractionable = Column(Boolean, default=True)
    required_count = Column(Integer, default=1)
    required_roles_list = Column(JSON, default=list)
    hardness = Column(Integer, default=3)


class SoldierRequest(Base):
    """Requests, notes, or flags the commander tracks per soldier.
    Soldiers can submit via Matrix bot; commanders can also add manually."""
    __tablename__ = 'soldier_requests'

    id = Column(Integer, primary_key=True)
    soldier_id = Column(Integer, ForeignKey('soldiers.id'))

    # 'LEAVE', 'ROLE_CHANGE', 'SWAP_REQUEST', 'NOTE', 'OTHER'
    request_type = Column(String, default='NOTE')
    description = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.now)

    # 'PENDING', 'APPROVED', 'REJECTED', 'NOTED'
    status = Column(String, default='PENDING')
    resolved_at = Column(DateTime, nullable=True)
    resolver_note = Column(String, nullable=True)

    soldier = relationship("Soldier", back_populates="requests")
