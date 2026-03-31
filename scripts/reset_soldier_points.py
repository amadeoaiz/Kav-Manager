"""
Utility: recompute soldier rate columns from TaskAssignment history.

Run from the project root:

    .\\venv_win\\Scripts\\python -m scripts.reset_soldier_points
"""

from src.core.database import SessionLocal, DB_PATH, init_db
from src.utils.maintenance import resync_soldier_rates


def main() -> None:
    init_db()

    db = SessionLocal()
    try:
        resync_soldier_rates(db)
        db.commit()
        print(f"Resynced soldier rates in {DB_PATH}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
