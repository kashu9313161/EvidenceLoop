# EvidenceLoop: project guide

**Understand a codebase before you change it.** Give EvidenceLoop a GitHub repository or a ZIP. It reads every
file, maps how they connect, explains the project in plain English, and points out what needs fixing, with the
exact lines as proof. Nothing from the repository is ever run.

## How it works

```
GitHub URL / ZIP
   │  ingestion/service.py   safe download or upload, every archive entry validated first,
   ▼                         atomic extraction, snapshot pinned to a commit SHA
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
| Coverage | What was read, indexed, skipped and why, plus how many imports resolved. |

## Safety and privacy

- Repository code is parsed, never executed; dependencies are never installed.
- ZIPs and GitHub archives are validated before anything is written: path traversal, absolute paths, symlinks,
  encrypted entries, duplicates, size (`MAX_FILE_UNCOMPRESSED_BYTES` 10 MB per file, 250 MB total), file count
  (5,000) and compression ratio (100×) limits, in `core/config.py`.
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
