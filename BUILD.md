# Building KavManager for Windows

## Prerequisites

1. Install Python 3.12 on Windows (not WSL)
2. Clone or copy the KavManager project to Windows
3. Open Command Prompt or PowerShell in the project directory

## Setup

```
python -m venv venv_win
venv_win\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
```

## Build

```
pyinstaller KavManager.spec
```

Build takes a few minutes. Output goes to `dist/KavManager/`.

## Output

The built application will be in `dist/KavManager/`. To distribute:

1. Zip the entire `dist/KavManager/` folder
2. Recipients unzip and run `KavManager.exe`

## Runtime behavior

On first run, the app creates a `data/` folder **next to the exe** containing:

- `app.db` — SQLite database (unit config, soldiers, tasks, schedules)
- `nio_store/` — Matrix bot encryption state
- `backups/` — automatic database backups

This means the app is fully portable — move the folder anywhere and it keeps its data.

## Troubleshooting

**"DLL load failed"**: Make sure you're using Python 3.12 (not 3.13+). PyQt6 wheels may not be available for newer Python versions yet.

**Missing modules at runtime**: If PyInstaller misses an import, add it to the `hiddenimports` list in `KavManager.spec` and rebuild.

**Anti-virus false positive**: Some AV software flags PyInstaller bundles. Add an exception for the `dist/KavManager/` folder.

**Bot not connecting**: The Matrix bot needs network access. If running behind a firewall, ensure HTTPS (port 443) is allowed to the Matrix homeserver.

**Bot encryption errors**: If the bot can't send or receive encrypted messages, delete the `data/nio_store/` folder and restart. If the problem persists, verify matrix-nio>=0.25.2 is installed (`pip show matrix-nio`).

---

## Updating the public repository

The private repo (`amadeoaiz/KavManager`) contains everything — docs, internal scripts, design decisions, dev tooling. The public repo (`amadeoaiz/Kav-Manager`) is a filtered mirror that excludes sensitive and internal files.

The sync script (`scripts/sync_public.sh`) automates this. It clones the public repo, replaces its contents with a filtered copy of the private repo, shows a diff, and pushes after confirmation.

### First-time setup

Generate a GitHub Personal Access Token for the public repo:

1. Go to https://github.com/settings/tokens
2. Create a **classic** token with `repo` scope
3. Run the script with the token — it gets saved for future use:

```bash
./scripts/sync_public.sh ghp_YourTokenHere
```

### Subsequent updates

```bash
./scripts/sync_public.sh
```

The script will show a summary of changes and ask for confirmation before pushing.

### What gets excluded from the public repo

The script uses rsync with an explicit exclude list. These are filtered out:

| Category | Excluded |
|----------|----------|
| Internal docs | `docs/`, `SESSION_HANDOFF.md` |
| Dev tooling | `.claude/`, `.cursor/`, `.cursorignore`, `scripts/`, `diag_windows.py` |
| Build artifacts | `dist/`, `build/`, `build.spec`, `__pycache__/` |
| Runtime data | `data/`, `nio_store/`, `*.db`, `*.db-journal` |
| Environments | `venv/`, `venv_*/`, `.venv/` |
| Secrets | `.env`, `*.token` |
| OS/editor | `.DS_Store`, `Thumbs.db`, `*.swp`, `*:Zone.Identifier` |

To change what's excluded, edit the `rsync` exclude list in `scripts/sync_public.sh`.
