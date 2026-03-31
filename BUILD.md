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
