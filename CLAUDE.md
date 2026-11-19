# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

EvidenceLoop V2 is a local web app: `web/` (React + Vite) talks to `api/main.py` (FastAPI), which serves the built
app from `web/dist`. The flow is ingestion → per-file parse (`analysis/pipeline.py`) → report
(`analysis/understanding/report.py`, stored in `analysis_reports` plus `graph_nodes`/`graph_edges`). The Streamlit
app was removed in V2. Optional AI features live in `intelligence/providers/` (one `Provider` interface; Anthropic via its SDK,
Gemini via REST over `httpx` with an injectable transport; a `FakeProvider` for tests) and are off without
`ANTHROPIC_API_KEY` or `GEMINI_API_KEY` (`EL_PROVIDER` picks when both are set). File explanations
(`analysis/understanding/explain.py`) are cached in `file_explanations` by content hash, prompt version and model;
`GET /api/explain` only reads the cache, `POST` writes (so other sites can't spend the key). Bump `PROMPT_VERSION`
when the prompt changes. The map layout (`web/src/mapLayout.ts`) is shared by the Overview preview and the Map.

Ask (`POST /api/ask`, `intelligence/ask.py`): `retrieval/router.py` sends lookups to `retrieval/lookups.py` (symbols,
calls, imports, graph; no AI) and everything else through `retrieval/evidence.py` (numbered, hashed, redacted items),
the answering model, then `verification/checks.py` (citation checks, then an `intelligence/evaluators` evaluator;
`LlmJudgeEvaluator` now, Jev later behind the same protocol). Provider calls go through `intelligence/sendlog.Metered`
(per-question `Budget`, logged to `provider_sends`); results go to `asks`, `ask_claims`, `ask_claim_evidence`. Tests use
`FakeProvider` and a fixed evaluator. In the UI, `CommandBox` (top right) calls `/api/search` and `GET /api/lookup`
(`answer_lookup`: no AI, nothing stored) as you type; `Chat` (bottom right) posts to `/api/ask`, loads history from
`/api/asks` and shows models from `/api/ai`; both render answers with `Answer.tsx`. New columns on existing tables go in
`ADDED_COLUMNS` in `storage/database.py`. What's next is in `docs/IMPLEMENTATION_PLAN.md`; how it works is in
`docs/PROJECT_GUIDE.md`.

When the report's shape changes, bump `REPORT_VERSION` in `report.py` so stored reports are rebuilt, and keep
`web/src/api.ts` types in step with it.

## Commands

Use the project venv (Windows: `.venv\Scripts\python.exe`). Run everything from the repo root; the code uses top-level
imports (`from core import config`, `from ingestion.service import ...`), and pytest sets `pythonpath = ["."]`.

```bash
pip install -e ".[dev]"
ruff check .                                   # lint (CI runs this; line length 100, py311 target)
pytest -q                                      # all tests (offline)
pytest tests/test_ingestion.py::test_reject_path_traversal -q   # single test

python -m cli.main serve                                  # web app on http://127.0.0.1:8000
python -m cli.main analyze https://github.com/owner/repo  # or owner/repo, or a local .zip
python -m cli.main report "<snapshot_id>"                 # headline, areas, issues as text
python -m cli.main coverage "<snapshot_id>"

uvicorn api.main:app --reload --port 8000                 # API with reload
cd web && npm install && npm run dev                      # UI with hot reload on :5173
cd web && npm run build                                   # what `serve` hosts
```

CI (`.github/workflows/ci.yml`) runs `ruff check .` and `pytest -q` on Ubuntu **and Windows**, Python 3.11 and 3.12, so
path handling must work on both.

## Architecture

**Flow:** `api/main.py` or `cli/main.py` → `ingestion/service.py` → `analysis/pipeline.py` →
`analysis/understanding/report.py` → `storage/database.py` (SQLite at `data/evidenceloop.db`), with extracted files
under `data/repositories/`. Snapshot roots are resolved with `storage.database.snapshot_root`, which survives a moved
project folder. All limits, supported extensions and ignored names live in `core/config.py`.

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
