import sys
import os

# Set paths
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, '..'))
src_path = os.path.join(project_root, 'src')

if src_path not in sys.path:
    sys.path.insert(0, src_path)

try:
    # This matches your src/core/database.py
    from core.database import SessionLocal 
    # This matches your src/utils/maintenance.py
    from utils.maintenance import MaintenanceManager 
    print(">>> All modules found.")
except ImportError as e:
    print(f">>> Import Error: {e}")
    sys.exit(1)

def main():
    db = SessionLocal()
    try:
        db_path = os.path.join(project_root, "data", "app.db")
        backup_dir = os.path.join(project_root, "data", "backups")
        
        manager = MaintenanceManager(db, db_path=db_path, backup_dir=backup_dir)
        manager.run_full_maintenance(tag="auto")
    except Exception as e:
        print(f">>> Execution Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()