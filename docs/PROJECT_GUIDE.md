# EvidenceLoop: project guide

**Understand a codebase before you change it.** Give EvidenceLoop a GitHub repository, a ZIP or a folder. It
reads every source file, maps how they connect, explains the project in plain English, and points out what needs fixing, with the
exact lines as proof. Nothing from the repository is ever run.

## How it works

```
GitHub URL / ZIP / folder
   │  ingestion/preflight.py  GitHub: commit SHA, then the whole file tree with sizes; decide
   │                          what to read BEFORE downloading (ingestion/rules.py)
   │  ingestion/fetch.py      fetch only those files: zipball (small repos), sparse git
   │                          checkout, or one download per file; checked again, published
   │                          atomically
   │  ingestion/archive.py    ZIP uploads: list entries, extract only eligible ones
   ▼  ingestion/service.py    snapshot pinned to a commit SHA, coverage rows for every file
Snapshot on disk + SQLite
   │  analysis/pipeline.py   per-file parse: Python syntax tree, JS/TS imports and exports,
   ▼                         full-text index, coverage per stage
Facts (symbols, imports, calls)
   │  analysis/understanding/report.py
   │     ├─ analysis/python/quality.py     complexity, routes, entry points, smells
   │     ├─ analysis/graph/resolver.py     imports → files (confirmed / inferred)
   │     ├─ analysis/graph/builder.py      graph, fan-in, impact, importance, cycles, areas
   │     ├─ analysis/understanding/profile.py   stack, manifests, README, wording helpers
   │     └─ analysis/understanding/insights.py  issue rules with evidence
   ▼
Report (one JSON document per snapshot, plus graph_nodes / graph_edges)
   │  api/main.py            jobs with progress, report, file, search; serves web/dist
   ▼
web/ (React)               Home → Workspace: Overview · Map · Issues · Files
```

## Concepts

| Term | Meaning |
|---|---|
| Snapshot | A repository at one commit. Everything shown belongs to a snapshot. |
| Area | A folder that groups related files (`src/` is skipped; monorepos descend to the project folder). |
| Connection | File A imports file B. **Confirmed**: only one file could match. **Inferred**: a best guess, drawn dashed. |
| Used by / fan-in | Distinct files that import a file, not counting tests. Bigger dots on the map. |
| Impact | Every file that depends on a file, directly or through others: what a change could break. |
| Role | What a file is for: entry point, API, UI, script, core, data models, config, package, test… |
| Issue | A problem found by a rule, with severity, why it matters, what to do, and the lines. **Confirmed** issues matched the code exactly; **likely** ones need a quick human check. |
| Coverage | What was read, indexed, skipped and why, plus how many imports resolved. For a big repository, "analysed N of M source files" and the folders left out. |
| Source file | A file EvidenceLoop reads: a supported type, not in a dependency/build folder, not data, models, media or archives, and at most 1 MB. Limits count only these. |
| Scope | For a repository with more source than one analysis takes: one folder, or `*` (the most important files: manifests and READMEs, entry points, then the shallowest paths). |

## Safety and privacy

- Repository code is parsed, never executed; dependencies are never installed.
- **Decide first, then fetch.** For GitHub, the preflight lists the tree and classifies every path before any
  file content is downloaded. Skipped at any depth: virtual environments (`venv`, `.venv`, `env`,
  `site-packages`, `.tox`), `node_modules`, build output (`dist`, `build`, `target`), caches, `vendor/` for JS and
  Go; datasets, model weights, media, documents and archives by extension; anything over
  `MAX_FILE_ANALYSIS_BYTES` (1 MB). Every skipped file is still listed in coverage with its reason.
- **Limits count source only**: `MAX_SOURCE_FILES` (5,000) and `MAX_SOURCE_BYTES` (250 MB), which
  `EL_MAX_SOURCE_FILES` / `EL_MAX_SOURCE_BYTES` can raise up to hard ceilings of 20,000 files and 500 MB. Over
  them, nothing is downloaded: the person picks a folder, or the most important files.
- **Fetching**: a zipball only when the whole repository is under 100 MB; otherwise `git` with a blobless,
  shallow, sparse checkout of exactly the chosen paths, hardened with `core.symlinks=false`, a null
  `core.hooksPath`, `protocol.file.allow=never`, `protocol.ext.allow=never`, no submodules, LFS filters off,
  `GIT_TERMINAL_PROMPT=0` and a timeout; `.git` is deleted afterwards. Without git (or if it fails), each file is
  downloaded from raw.githubusercontent.com, 8 at a time, with its size checked against the tree as it streams.
  Whatever fetched them, the files on disk are checked again (paths, reserved names, case clashes, links, exactly
  the chosen set) before the folder is published with one `os.replace`.
- **Uploads**: a ZIP (up to 1 GB, streamed to a temp file, never held in memory) is listed first and only
  eligible entries are extracted, with the traversal, encryption, duplicate and compression-ratio (100×) checks
  on what is extracted. A folder upload is filtered in the browser with the same rules (from `GET /api/config`)
  and checked again on the server. Asking again would mean uploading again, so an upload over the limits keeps
  its most important files.
- A `GITHUB_TOKEN`, if set, is sent only to api.github.com (5,000 requests an hour instead of 60).
- Possible credentials are shown redacted.
- Everything runs locally. No repository content leaves the machine (the planned Ask feature will send only cited
  snippets, recorded in a send log).

## Running it

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cd web && npm install && npm run build && cd ..

python -m cli.main serve             # http://127.0.0.1:8000
```

Development with hot reload: `uvicorn api.main:app --reload --port 8000` and, in `web/`, `npm run dev`
(http://localhost:5173, API calls proxied to port 8000).

Command line without the browser:

```bash
python -m cli.main analyze pallets/itsdangerous
python -m cli.main analyze path/to/project.zip
python -m cli.main analyze krishnaik06/The-Grand-Complete-Data-Science-Materials --scope "ML Projects"
python -m cli.main list
python -m cli.main report "github:pallets/itsdangerous@<sha>"
```

Checks: `ruff check .` and `pytest -q` (offline), and `npm run build` in `web/`.

## Adding an issue rule

1. Collect the fact you need in `analysis/python/quality.py` (or from the graph).
2. Write a function in `analysis/understanding/insights.py` that returns `Insight` objects with a severity, a
   plain-language `why` and `suggestion`, an honest `confidence`, and `Evidence` (path and lines).
3. Add it to `generate()`, bump `REPORT_VERSION` in `report.py` so stored reports rebuild, and add a test in
   `tests/test_understanding.py`.

## What it is not (yet)

Not a linter replacement, not a security scanner, and not an AI chat over your files. Questions answered with
verified evidence (Ask), deeper scanners, AI summaries and the Obsidian export are next; see
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).
