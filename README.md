# EvidenceLoop

**Understand a codebase before you change it.**

Paste a GitHub repository (or drop a ZIP). EvidenceLoop reads every file, maps how they connect, explains the
project in plain English, and points out what needs fixing, with the exact lines as proof. Nothing from the
repository is ever run.

- **Overview:** what the project is, how it's organised, where it starts, what to fix first.
- **Map:** an Obsidian-style graph of every file and import. Hover to trace, click for details, and see everything a
  change would reach.
- **Issues:** import cycles, overly complex functions, silenced errors, untested hubs, undeclared or unpinned
  dependencies and more, ranked by how far each one spreads.
- **Files:** the code with each file's role, description, functions and complexity.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cd web && npm install && npm run build && cd ..
python -m cli.main serve             # then open http://localhost:8000
```

Checks: `ruff check .`, `pytest -q` (offline) and `npm run build` in `web/`.

## Learn more

- [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md): how it works, concepts, safety, adding a rule.
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): what's built and what comes next.
