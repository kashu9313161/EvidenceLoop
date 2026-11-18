# EvidenceLoop

**Ask a codebase questions and get answers you can check.**

EvidenceLoop is a local-first codebase intelligence tool. It turns a public GitHub repository or a ZIP into an immutable,
commit-pinned snapshot. It analyzes the snapshot without ever running its code, maps how the code connects in an
Obsidian-style graph, and answers questions with claims that cite exact files and lines. Each claim is checked before
you see it. It also shows what it did **not** analyze, and it never collapses everything into a single "health score".

- **What it is and how it works:** [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md)
- **What we're building, in order:** [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows. On macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

python -m cli.main ingest https://github.com/pallets/itsdangerous
streamlit run app/streamlit_app.py
```

Run the checks with `ruff check .` and `pytest -q`. The tests run fully offline.

## Status

Ingestion, ingestion hardening and the app shell (add a repository, snapshot overview, coverage ledger, light and
dark themes) are done. Next up: Python code analysis, the knowledge graph, and the Obsidian-style graph page.
See the [implementation plan](docs/IMPLEMENTATION_PLAN.md) for everything after that.
