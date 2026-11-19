# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

EvidenceLoop V2 is a local web app: `web/` (React + Vite) talks to `api/main.py` (FastAPI), which serves the built
app from `web/dist`. The flow is ingestion (`ingestion/preflight.py` lists a GitHub tree and decides what to read
before downloading; `ingestion/fetch.py` fetches only that: zipball under 100 MB, else a hardened sparse `git`
checkout, else per-file raw downloads; `ingestion/archive.py` extracts only eligible ZIP entries; the rules are in
`ingestion/rules.py`, the limits in `core/config.py` and count source files only; over them a job ends with stage
`needs_scope` and `POST /api/analyze` takes `scope`: a folder or `*`) → per-file parse (`analysis/pipeline.py`) → report
(`analysis/understanding/report.py`, stored in `analysis_reports` plus `graph_nodes`/`graph_edges`). The Streamlit
app was removed in V2. Optional AI features live in `intelligence/providers/` (one `Provider` interface; Anthropic via its SDK,
Gemini via REST over `httpx` with an injectable transport; a `FakeProvider` for tests) and are off without
`ANTHROPIC_API_KEY` or `GEMINI_API_KEY` (`EL_PROVIDER` picks when both are set). File explanations
(`analysis/understanding/explain.py`) are cached in `file_explanations` by content hash, prompt version and model;
`GET /api/explain` only reads the cache, `POST` writes (so other sites can't spend the key). Bump `PROMPT_VERSION`
when the prompt changes. Function cards (`GET/POST /api/explain/function`, same split) answer the same five
questions for a Python function from `quality.py` facts (parameters, returns, raises, side effects), the call graph
(`analysis/understanding/calls.py`, stored as `calls` edges in `graph_edges`; read with `report.call_edges`) and
call-site examples (`function_facts.py`); they are cached in `function_explanations` by function text + call sites,
`FUNCTION_PROMPT_VERSION` and model. Every row carries its source (code, docstring, ai, ai-checked). The map layout (`web/src/mapLayout.ts`) is shared by the Overview preview and the Map.

Ask (`POST /api/ask`, `intelligence/ask.py`): `retrieval/router.py` sends lookups to `retrieval/lookups.py` (symbols,
calls, imports, graph; no AI) and everything else through `retrieval/evidence.py` (numbered, hashed, redacted items),
the answering model, then `verification/checks.py` (citation checks, then an `intelligence/evaluators` evaluator:
`LlmJudgeEvaluator`, or `JevEvaluator` via the official `typesafe-sdk` when `EL_EVALUATOR=jev`; chosen in
`evaluators/choice.py`; Jev tests mock the SDK with an `httpx2.MockTransport`). Gate A (`evaluation/gate_a/run.py`,
`metrics.py`) scores evaluators against `candidates.yaml` human labels; the decision lives in `docs/decisions/gate-a.md`,
and calibrated thresholds go in `Thresholds` defaults. Provider calls go through `intelligence/sendlog.Metered`
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
- GitHub: resolve the default branch to a commit SHA, list the tree (folder by folder if GitHub truncates it, never
  descending into `node_modules`/`venv`), build a `Plan`, then fetch only its selected files (`fetch.fetch_plan`).
  Only public repos are accepted. `ingest_github_repository` takes an `httpx` `transport` (one fake serves
  api.github.com and raw.githubusercontent.com; see `github_transport` in `tests/test_ingestion.py`).
- `safe_extract_archive` (`ingestion/archive.py`) validates **every entry before writing anything**. It rejects
  traversal, absolute paths, Windows reserved names, duplicate and case-insensitive-duplicate paths and too many
  entries; for the files it extracts, encryption, broken metadata and high compression ratios. Symlinks and special
  files are recorded but not created. Skipped files (with `skip_reason`) are never decompressed and don't count toward
  limits. Over the source limits an upload keeps its most important files (`rules.LEFT_OUT`). Eligible files are
  recorded as `pending`.
- `atomic_extract_archive` extracts into a sibling temp dir and publishes it with one `os.replace`, so a failure leaves
  no partial snapshot.
- Snapshots are immutable and content-addressed: `github:{owner}/{name}@{sha}` → `data/repositories/{owner}_{name}/{sha}`,
  a scoped one adds `#folder` or `#top` (stored in `snapshots.scope`), ZIPs → `zip-{sha256[:24]}` →
  `data/repositories/uploaded_zips/...`, folders → `folder-{manifest digest[:24]}`. Re-ingesting returns `already_downloaded: true`.
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
- Source limits (`MAX_SOURCE_FILES`, `MAX_SOURCE_BYTES`, `MAX_UPLOAD_BYTES`, `HARD_MAX_*`) are read from
  `core.config` at call time, so tests monkeypatch `config`; the per-file limit is patched on `ingestion.rules`.
  git is never run in tests: pass `run=` (a fake `subprocess.run`) and `git=` to `ingest_github_repository`.
- `data/` is gitignored and holds extracted third-party repos with their own test suites. pytest (`norecursedirs`) and
  ruff (`extend-exclude`) skip it; keep it that way.
