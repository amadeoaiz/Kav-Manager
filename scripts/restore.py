import os
import shutil
import sys

# Setup Paths
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, '..'))
data_dir = os.path.join(project_root, 'data')
backup_dir = os.path.join(data_dir, 'backups')
live_db = os.path.join(data_dir, 'app.db')

def list_backups():
    if not os.path.exists(backup_dir):
        print("❌ No backup directory found.")
        return []
    backups = sorted([f for f in os.listdir(backup_dir) if f.endswith('.db')], reverse=True)
    return backups

def restore_backup(filename):
    source = os.path.join(backup_dir, filename)
    
    # 1. Create a safety copy of the current broken DB before overwriting
    safety_copy = live_db + ".pre_restore"
    if os.path.exists(live_db):
        shutil.copy2(live_db, safety_copy)
    
    # 2. Perform the restore — remove WAL/SHM files so SQLite doesn't
    #    replay stale write-ahead log data on top of the restored backup.
    try:
        shutil.copy2(source, live_db)
        for suffix in ("-wal", "-shm"):
            aux = live_db + suffix
            if os.path.exists(aux):
                os.remove(aux)
        print(f"✅ Successfully restored from: {filename}")
        print(f"📦 A safety copy of your old DB was saved as 'app.db.pre_restore'")
    except Exception as e:
        print(f"❌ Restore failed: {e}")

if __name__ == "__main__":
    print("--- KavManager Restore Utility ---")
    backups = list_backups()
    
    if not backups:
        print("No backups available to restore.")
        sys.exit()

    print("\nAvailable Backups (Newest First):")
    for i, b in enumerate(backups):
        print(f"[{i}] {b}")

    choice = input("\nEnter the number of the backup to restore (or 'q' to quit): ")
    if choice.isdigit() and int(choice) < len(backups):
        selected = backups[int(choice)]
        confirm = input(f"Are you sure you want to overwrite the live DB with {selected}? (y/n): ")
        if confirm.lower() == 'y':
            restore_backup(selected)
    else:
        print("Exiting.")