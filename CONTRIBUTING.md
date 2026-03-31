# Contributing to KavManager

## Dev Environment Setup

KavManager is developed on Windows (WSL recommended for the dev workflow).

```bash
git clone https://github.com/yourusername/KavManager.git
cd KavManager
python -m venv venv
source venv/bin/activate        # Linux/WSL
# venv\Scripts\activate         # Windows CMD
pip install -r requirements.txt
```

To run the app (on WSL, set the Qt platform):
```bash
QT_QPA_PLATFORM=xcb python main.py
```

On Windows:
```bash
python main.py
```

## Running Tests

```bash
python -m pytest tests/ -v
```

## Code Style

Standard Python. No linter is configured — just keep it consistent with the existing code.

## Submitting PRs

1. Fork the repo and create a feature branch
2. Make your changes
3. Run the test suite and make sure all tests pass
4. Open a PR against `master` with a clear description of what and why

## The LP Solver

The allocation engine uses a two-stage block-based LP optimizer (PuLP + CBC). It's the most complex part of the codebase. Before making changes to `src/core/lp_solver.py` or `src/core/lp_weights.py`, read `docs/LP_FORMULATION.md` for the full mathematical specification.
