# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

EvidenceLoop V1 is a local-first, evidence-grounded codebase intelligence tool. **Ingestion** (Milestone 1.5) and the
**Streamlit app shell** are implemented. The app (`app/streamlit_app.py` with pages in `app/app_pages/`) is wired to
ingestion and shows the snapshot overview and the coverage ledger. The other pages are labelled placeholders.
`analysis/` (python/javascript/typescript/graph/scanners/coverage), `retrieval/`, `verification/`, `evaluation/`,
`findings/`, `intelligence/` (providers/evaluators) and `obsidian/` are empty package placeholders. The FastAPI app
(`api/main.py`) exposes only `/` and `/health`. The build order and acceptance criteria for everything else are in
`docs/IMPLEMENTATION_PLAN.md`, and the product explanation is in `docs/PROJECT_GUIDE.md`.

## Commands

Use the project venv (Windows: `.venv\Scripts\python.exe`). Run everything from the repo root; the code uses top-level
imports (`from core import config`, `from ingestion.service import ...`), and pytest sets `pythonpath = ["."]`.

```bash
pip install -e ".[dev]"
ruff check .                                   # lint (CI runs this; line length 100, py311 target)
pytest -q                                      # all tests (offline)
pytest tests/test_ingestion.py::test_reject_path_traversal -q   # single test

python -m cli.main ingest https://github.com/owner/repo   # or a local .zip path
python -m cli.main coverage "<snapshot_id>"               # per-file pending/skipped status
python -m cli.main snapshot "<snapshot_id>"               # snapshot row as JSON

uvicorn api.main:app --port 8000
streamlit run app/streamlit_app.py
```

CI (`.github/workflows/ci.yml`) runs `ruff check .` and `pytest -q` on Ubuntu **and Windows**, Python 3.11 and 3.12, so
path handling must work on both.

## Architecture

**Flow:** `cli/main.py` → `ingestion/service.py` → `storage/database.py` (SQLite at `data/evidenceloop.db`), with
extracted files under `data/repositories/`. All limits, supported extensions and ignored names live in `core/config.py`.

**Ingestion (`ingestion/service.py`)** treats every repository as untrusted input. Nothing is executed:
- GitHub: resolve the default branch to a commit SHA through the GitHub API, then stream that zipball, aborting past
  `MAX_ARCHIVE_DOWNLOAD_BYTES`. Only public repos are accepted. `github_download` takes an `httpx` `transport` so tests
  can inject `httpx.MockTransport` instead of using the network.
- `safe_extract_archive` validates **every entry before writing anything**. It rejects traversal, absolute paths,
  Windows reserved names, encrypted or corrupt entries, duplicate and case-insensitive-duplicate paths, oversize files or
  totals, too many files and high compression ratios. Symlinks and special files are recorded but not created.
  Unsupported or ignored files are recorded as `skipped` (with `skip_reason`) and don't count toward size limits.
  Eligible files are recorded as `pending`, meaning they wait for the future analysis stages.
- `atomic_extract_archive` extracts into a sibling temp dir and publishes it with one `os.replace`, so a failure leaves
  no partial snapshot.
- Snapshots are immutable and content-addressed: `github:{owner}/{name}@{sha}` → `data/repositories/{owner}_{name}/{sha}`,
  and ZIPs → `zip-{sha256[:24]}` → `data/repositories/uploaded_zips/...`. Re-ingesting returns `already_downloaded: true`.
  A directory with no DB row (left by a crash between extract and persist) is deleted and re-ingested. If persisting
  fails, the extracted dir is removed. The repository, snapshot and file rows are written in a single transaction.

**Storage (`storage/database.py`)**: `get_connection()` is a context manager that commits, rolls back or closes as
needed. The schema is created lazily by `initialize_database()` (idempotent `CREATE ... IF NOT EXISTS`). It already
contains tables for future stages: `files_fts` (FTS5) and `graph_nodes`/`graph_edges`, whose edges carry line ranges,
`extraction_method` and `evidence_status`. New stages should populate these rather than add parallel tables.

## Testing gotchas

- Tests must stay offline. Build archives in memory (`make_zip`, `make_zip_entries`) and use `github_transport(...)` to
  fake the GitHub API.
- Use the `isolated_storage` fixture for anything that touches disk or the DB. It monkeypatches
  `config.DATABASE_PATH` (read at call time) **and** `service.REPOSITORIES_DIR` (imported by name into the service
  module). Any new module that imports a path constant by name needs the same patching in tests.
- Limits are tested by monkeypatching the constants imported into `ingestion.service`, not those in `core.config`.
- `data/` is gitignored and holds extracted third-party repos with their own test suites. pytest (`norecursedirs`) and
  ruff (`extend-exclude`) skip it; keep it that way.
