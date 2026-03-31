from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
import os
from .models import Base, Role, UnitConfig, MissionRequirement
from .paths import get_data_dir

DB_DIR = get_data_dir()
DB_PATH = os.path.join(DB_DIR, "app.db")

DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

@event.listens_for(engine, "connect")
def _set_sqlite_wal(dbapi_conn, connection_record):
    """Enable WAL mode so the desktop app and Matrix bot can share the DB."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


_SEED_ROLES = [
    # Base
    ("Soldier",                 "Universal base role — every active soldier",       None),
    # Command
    ("Officer",                 "Commissioned officer",                              "Soldier"),
    ("Sargent",                 "Sergeant — senior NCO",                             "Soldier"),
    ("Squad Commander",         "Leads a rifle squad",                               "Sargent"),
    # Medical
    ("Medic",                   "Combat medical technician",                         "Soldier"),
    # Observation & ISR
    ("Observer",                "Forward observation specialist",                    "Soldier"),
    ("Navigator",               "Navigation and map reading specialist",             "Observer"),
    # Vehicles
    ("Driver",                  "Licensed vehicle operator",                         "Soldier"),
    ("Operational Driver",      "Licensed driver for operational vehicles",          "Driver"),
    # Weapons & Fire
    ("Negevist",                "Light machine gun (Negev) operator",                "Soldier"),
    ("Matolist",                "Anti-tank missile system operator",                 "Soldier"),
    ("Kala",                    "Heavy machine gun (Kala) operator",                 "Soldier"),
    ("SmartShooter",            "Smart optics / fire control system operator",       "Observer"),
    ("Explosives",              "Combat engineering / explosives handler",           "Soldier"),
    # Drones
    ("Regular Drone Operator",  "Operates standard ISR / recon drones",             "Observer"),
    ("FPV Drone Operator",      "Operates first-person-view attack drones",         "Regular Drone Operator"),
    # Staff
    ("Mashak-Gil",              "Age-group coordinator / welfare role",              "Soldier"),
    ("Kavan-Gil",               "Age-group assistant coordinator",                   "Mashak-Gil"),
    ("Magist",                  "Logistics and supply specialist",                   "Soldier"),
]


def init_db():
    """Initializes the database, creates all tables, runs migrations, and seeds initial data."""
    Base.metadata.create_all(bind=engine)
    _migrate_schema()

    db = SessionLocal()
    try:
        _seed_roles(db)
        _seed_unit_config(db)
    finally:
        db.close()

    print(f"Database initialized at: {DB_PATH}")


def _migrate_schema():
    """
    Adds columns that were introduced after the initial schema was created.
    Uses ALTER TABLE ... ADD COLUMN; silently skips columns that already exist.
    Safe to run on every startup — idempotent.

    Note: some columns (e.g. legacy codename fields) are kept only so older
    databases can still be opened; new code no longer relies on them.
    """
    migrations = [
        # soldiers
        # legacy codename column kept for backward-compatible DB upgrades only
        ("soldiers", "codename",            "TEXT"),
        ("soldiers", "is_active_in_kav",    "BOOLEAN DEFAULT 1"),
        ("soldiers", "present_days_count",  "REAL DEFAULT 0.0"),
        ("soldiers", "active_reserve_days", "INTEGER DEFAULT 0"),
        ("soldiers", "telegram_id",         "TEXT"),  # legacy (kept for existing DBs)
        # tasks
        # legacy codename column kept for backward-compatible DB upgrades only
        ("tasks", "codename",               "TEXT"),
        ("tasks", "base_weight",            "REAL DEFAULT 1.0"),
        ("tasks", "is_active",              "BOOLEAN DEFAULT 1"),
        ("tasks", "coverage_status",        "TEXT DEFAULT 'OK'"),
        ("tasks", "readiness_minutes",      "INTEGER DEFAULT 0"),
        ("tasks", "required_roles_list",    "TEXT"),
        ("tasks", "required_count",         "INTEGER DEFAULT 1"),
        ("tasks", "is_fractionable",        "BOOLEAN DEFAULT 1"),
        ("tasks", "hardness",               "INTEGER DEFAULT 3"),
        # task_assignments
        ("task_assignments", "start_time",              "DATETIME"),
        ("task_assignments", "end_time",                "DATETIME"),
        ("task_assignments", "final_weight_applied",    "REAL"),
        ("task_assignments", "pending_review",          "BOOLEAN DEFAULT 0"),
        ("task_assignments", "is_pinned",                "BOOLEAN DEFAULT 0"),
        # unit_config — export settings
        ("unit_config", "google_sheets_id",   "TEXT"),
        ("unit_config", "google_creds_path",  "TEXT"),
        ("unit_config", "default_export_dir", "TEXT"),
        # unit_config — legacy (kept for existing DBs)
        ("unit_config", "telegram_bot_token",              "TEXT"),
        ("unit_config", "swap_approval_timeout_minutes",   "INTEGER DEFAULT 15"),
        ("unit_config", "commander_soldier_id",            "INTEGER REFERENCES soldiers(id)"),
        # soldiers — matrix
        ("soldiers", "matrix_id",                "TEXT"),
        # unit_config — matrix bot
        ("unit_config", "matrix_homeserver_url",  "TEXT"),
        ("unit_config", "matrix_bot_user",        "TEXT"),
        ("unit_config", "matrix_bot_token",       "TEXT"),
        # Phase 1A: eligibility — per-task exclusion + chain of command
        ("tasks",       "excluded_soldier_ids",   "TEXT DEFAULT '[]'"),
        ("tasks",       "include_commander",      "BOOLEAN DEFAULT 0"),
        ("unit_config", "command_chain",           "TEXT DEFAULT '[]'"),
        # Reserve period boundaries
        ("unit_config", "reserve_period_start",    "DATETIME"),
        ("unit_config", "reserve_period_end",      "DATETIME"),
        # Soldier language preference
        ("soldiers", "preferred_language",         "TEXT DEFAULT 'en'"),
        # Notification preferences (JSON)
        ("soldiers", "notification_prefs",         "TEXT"),
    ]

    # Create new tables that didn't exist in earlier schema versions
    for create_sql in [
        """CREATE TABLE IF NOT EXISTS soldier_requests (
                id INTEGER PRIMARY KEY,
                soldier_id INTEGER REFERENCES soldiers(id),
                request_type TEXT DEFAULT 'NOTE',
                description TEXT NOT NULL,
                created_at DATETIME,
                status TEXT DEFAULT 'PENDING',
                resolved_at DATETIME,
                resolver_note TEXT
           )""",
        """CREATE TABLE IF NOT EXISTS mission_requirements (
                id INTEGER PRIMARY KEY,
                date_from DATETIME NOT NULL,
                date_to   DATETIME NOT NULL,
                label     TEXT,
                min_soldiers INTEGER DEFAULT 1,
                required_roles TEXT,
                note TEXT
           )""",
        """CREATE TABLE IF NOT EXISTS gear_items (
                id INTEGER PRIMARY KEY,
                soldier_id INTEGER NOT NULL REFERENCES soldiers(id),
                item_name TEXT NOT NULL,
                quantity INTEGER DEFAULT 1,
                serial_number TEXT
           )""",
        """CREATE TABLE IF NOT EXISTS team_gear_items (
                id INTEGER PRIMARY KEY,
                item_name TEXT NOT NULL,
                quantity INTEGER DEFAULT 1,
                serial_number TEXT,
                notes TEXT
           )""",
        """CREATE TABLE IF NOT EXISTS draft_intervals (
                id INTEGER PRIMARY KEY,
                soldier_id INTEGER REFERENCES soldiers(id),
                start_time DATETIME,
                end_time   DATETIME,
                status     TEXT
           )""",
        """CREATE TABLE IF NOT EXISTS task_templates (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                start_time_of_day TEXT NOT NULL,
                end_time_of_day TEXT NOT NULL,
                crosses_midnight BOOLEAN DEFAULT 0,
                is_fractionable BOOLEAN DEFAULT 1,
                required_count INTEGER DEFAULT 1,
                required_roles_list TEXT,
                hardness INTEGER DEFAULT 3
           )""",
    ]:
        with engine.connect() as conn:
            try:
                conn.execute(__import__('sqlalchemy').text(create_sql))
                conn.commit()
            except Exception:
                pass

    with engine.connect() as conn:
        for table, column, col_def in migrations:
            try:
                conn.execute(
                    __import__('sqlalchemy').text(
                        f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"
                    )
                )
                conn.commit()
            except Exception:
                # Column already exists — skip silently
                pass


def _seed_roles(db):
    """Inserts missing roles. Idempotent."""
    existing = {r.name: r for r in db.query(Role).all()}

    for name, description, _ in _SEED_ROLES:
        if name not in existing:
            role = Role(name=name, description=description)
            db.add(role)
            existing[name] = role

    db.flush()

    for name, _, parent_name in _SEED_ROLES:
        if parent_name and parent_name in existing:
            existing[name].parent_role_id = existing[parent_name].id

    db.commit()


def _seed_unit_config(db):
    """Creates the default UnitConfig row if it doesn't exist. Idempotent."""
    if db.query(UnitConfig).first() is None:
        db.add(UnitConfig())
        db.commit()


def get_db():
    """Yields a database session, ensuring it is closed after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
