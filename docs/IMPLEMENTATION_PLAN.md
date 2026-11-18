# EvidenceLoop V1: implementation plan

**Version 1.1, November 17, 2026.** Companion to the EvidenceLoop V1 PRD.
Build window: November 17, 2026 to January 4, 2027, at roughly 15 to 20 hours a week.
For what EvidenceLoop is and how it works, see [PROJECT_GUIDE.md](PROJECT_GUIDE.md).

**Contents:**
[1. Current state](#1-current-state) ·
[2. Changes from the previous draft](#2-changes-from-the-previous-draft) ·
[3. Architecture](#3-architecture) ·
[4. Configuration](#4-configuration) ·
[5. Milestones](#5-milestones) ·
[6. Evaluator contract and status rule](#6-evaluator-contract-and-status-rule) ·
[7. Timeline](#7-timeline) ·
[8. Risks](#8-risks) ·
[9. Inputs needed](#9-inputs-needed) ·
[10. Definition of done for V1](#10-definition-of-done-for-v1)

---

## 1. Current state

| Area | Status |
|---|---|
| M1 ingestion: GitHub API with commit SHA, ZIP, SQLite, coverage | Done |
| M1.5 hardening: two-phase validation, atomic extraction, 60 security tests, CI | Done, commit `79b892a` on `m1.5-ingestion-hardening` |
| Streamlit shell: Add repository, Overview, Coverage, placeholders, snapshot in URL | Done, commit `dbdcfbb` |
| Step 0: docs (this plan, project guide, README, CLAUDE.md status) | Done, commit `dbdcfbb` |
| Step 0b: light and dark themes | Done, commit `dbdcfbb` |
| M2 code analysis: Python symbols, imports and calls; JS/TS imports and exports; full-text index; stage columns | Done (111 tests pass) |
| Schema: `graph_nodes`, `graph_edges` | Tables exist, empty until M3 |
| `analysis`, `retrieval`, `verification`, `intelligence`, `obsidian` packages | Empty |

Prototype code to port from `PROJECTS/evidenceloop/evidenceloop/`:

| File | What it gives us |
|---|---|
| `chunking.py` | Splitting Python code by syntax tree |
| `graph.py` | Import graph, including relative imports and `src/` layouts |
| `analyze.py` | `parse_claims`, `verify_citations` |
| `evaluators.py` | The `Evaluator` protocol |
| `llm.py` | `FakeLLM` and an OpenAI-compatible client |
| `obsidian.py` | Vault writer |

## 2. Changes from the previous draft

1. **Gate A moves to week 2.** A minimal Jev adapter and the 40-case spike run during M3, so the evaluator thresholds and Jev's role are known before Ask is built. The Jev docs are needed now, not at M6.
2. **A plain-LLM baseline (A0) is added to the benchmark.** It answers the question "why not just give a chatbot the repository?"
3. **Review findings are benchmarked, not only Q&A.** Top-10 findings are labelled blind, for the pipeline and for A0.
4. **Graph-derived signals in M3** (cycles, heavily depended-on hubs, untested hubs; the graph's amber ring for untested hubs waits for M7), and **commit-history hotspots in M7** through the GitHub API, reported as not run when unavailable.
5. **A model per task, and run budgets.** Ask and Review use separate models, and each run has hard token and cost caps.
6. **Structured evaluator judgment.** The evaluator returns support, contradiction and sufficiency instead of one score, so "not supported" and "contradicted" lead to different statuses.
7. **Lean docs first.** Step 0 writes the essentials, and each milestone adds its own section.

## 3. Architecture

**Pipeline:** ingest → snapshot → analyze (syntax trees, full-text index, graph, signals, coverage) → ask or review (route, evidence package, LLM) → verify (deterministic checks, then evaluator) → show (graph, cards, vault).

**Rules that hold everywhere**
- Deterministic code finds facts and decides status. The LLM explains and proposes. The evaluator judges narrow questions.
- One service layer serves Streamlit, the CLI and later FastAPI.
- Everything is tied to a snapshot and stored in SQLite, so any run can be replayed.

**Package layout.** The repository keeps its flat layout: top-level packages imported from the repo root, as in `from core import config`.

```
core/           config, budget, logging
ingestion/      done
storage/        SQLite schema, graph loading
analysis/       python/, javascript/, graph/, scanners/, pipeline.py
retrieval/      router.py, search.py, evidence.py
verification/   verifier.py, status.py
intelligence/   providers/, evaluators/, prompts/
findings/       review.py
obsidian/       exporter.py
evaluation/     datasets/, runs.py, report.py
app/            streamlit_app.py, app_pages/, components.py, state.py, data.py, graph_view.py
cli/            main.py
api/            main.py (FastAPI, M9)
tests/
docs/           PROJECT_GUIDE.md, IMPLEMENTATION_PLAN.md, decisions/
```

## 4. Configuration

| Variable | Purpose | Default |
|---|---|---|
| `EL_LLM_PROVIDER` | `claude`, `openai_compat` or `fake` | `claude` |
| `ANTHROPIC_API_KEY` | Claude access | none |
| `EL_MODEL_ASK` | Model for Ask | `claude-sonnet-5-5` |
| `EL_MODEL_REVIEW` | Model for Review | `claude-opus-5-5` |
| `EL_OPENAI_BASE_URL`, `EL_OPENAI_API_KEY` | OpenAI-compatible provider | none |
| `EL_EVALUATOR` | `jev` or `llm_judge` | set by Gate A |
| `EL_JEV_URL`, `EL_JEV_API_KEY` | Jev access | none |
| `EL_JEV_VERSION` | `1.13.0` or `1.14.0`, pinned | set by Gate A |
| `EL_T_SUPPORT`, `EL_T_SUFFICIENCY`, `EL_T_CONTRADICTION` | Status thresholds | 0.80, 0.70, 0.50 until calibrated |
| `EL_MAX_EVIDENCE_ITEMS` | Evidence package cap | 12 |
| `EL_MAX_RUN_TOKENS`, `EL_MAX_RUN_COST_USD` | Per-run budget | 200000, 1.00 |
| `EL_GITHUB_TOKEN` | Higher rate limits and history hotspots | none |
| `EL_HISTORY_COMMITS` | Commits scanned for hotspots | 100 |

The defaults for thresholds, caps and evidence size are starting points. Adjust them from Gate A and benchmark data. Keys live only in environment variables, never in the repository.

## 5. Milestones

### Step 0: lean docs and commit (week 1)

**Goal:** the repository explains itself before more code lands.

**Tasks**
- [x] Commit the current UI work.
- [x] `README.md`: pitch, quick start, links to both docs.
- [x] `docs/PROJECT_GUIDE.md`: pitch, problem, how it works, glossary, security and privacy (with the real ZIP limits from `core/config.py`), configuration, how to run, status table. *(Already written in full, including the graph, asking, review and FAQ sections. Each milestone updates its section when it ships.)*
- [x] `docs/IMPLEMENTATION_PLAN.md`: this plan, exported to Markdown.
- [x] Update the project status in `CLAUDE.md`.

**Done when:** both docs render on GitHub and every number in them matches the code.

### Step 0b: light and dark themes (week 1). Done

**Goal:** proper themes using Streamlit's built-in Light, Dark and System switch.

**Tasks**
- [x] `.streamlit/config.toml` adapted from the shadcn-dual template: a shared theme with primary colour `#2563eb`, full light and dark sections, and chart colours per mode.
- [x] Change `toolbarMode` from `minimal` to `viewer` so the settings menu shows. Keep `maxUploadSize` at 100.
- [x] A sidebar caption explaining where the theme switch is.
- [ ] Code that draws its own colours (graph, badges) reads the current theme and avoids hard-coded white or black. *(Applies from M4.)*

**Done when:** every page is readable in both modes, checked in the browser.

**Tests:** AppTest smoke run. The config parses and contains both theme sections (`tests/test_theme_config.py`).

### M2: code analysis (week 1). Done

**Goal:** turn each pending file into symbols with exact line ranges.

**Tasks**
- [x] `analysis/python/extractor.py`: `extract_file(path, source)` returns `FileFacts`.
  - Symbols: module, class, function, method. Each with name, qualified name, line range, decorators, bases and whether it has a docstring.
  - Imports: module, names, alias, line.
  - Calls: callee text, line, enclosing symbol.
  - Port the chunking rules.
  - A `SyntaxError` marks the file `failed` with a reason.
- [x] `analysis/javascript/shallow.py`: `import`, `require` and `export` for `.js .jsx .ts .tsx .mjs .cjs`.
- [x] `analysis/models.py`: Pydantic models `Symbol`, `ImportRef`, `CallRef`, `FileFacts`.
- [x] Full-text: insert file text into `files_fts`, and add `symbols_fts`.
- [x] `analysis/pipeline.py`: `analyze_snapshot(snapshot_id)`. It's idempotent: it deletes that snapshot's rows and rewrites them in one transaction.
- [x] CLI `analyze` command. An Analyze button with progress on the Overview page.
- [x] Home runs analysis right after ingesting. Overview shows a code-structure browser (file → symbols with line ranges, imports, code view).
- [x] New file status `indexed`: docs and config are searchable but get no code analysis, so they are never counted as "analyzed".

**Data**
- `file_stages(snapshot_id, relative_path, stage, status, reason)`. Stages: `python_ast`, `js_shallow`, `fts`, `graph`, `scanners`, `history`.
- `symbols_fts(snapshot_id, symbol_id, qualified_name, path, start_line, end_line, text)`

**Done when:** itsdangerous analyzes with every `.py` file `analyzed`, line ranges spot-check correctly, and the Coverage page shows stage columns.

**Tests:** nested classes, async functions, decorators, a syntax-error file, line ranges, rerun with no duplicates, JS import forms.

### M3: knowledge graph and graph signals (week 2)

**Goal:** relationships with provenance, plus the first deterministic signals.

**Tasks**
- [ ] `analysis/graph/builder.py`.
  - Nodes: file, module, class, function. Finding and answer nodes are added later.
  - Edges: imports, defines, calls, inherits, tests.
- [ ] Resolution tiers:
  - **confirmed:** the import resolves inside the repository, and the call matches the imported name.
  - **inferred:** matched by name only.
  - Dynamic calls get no edge and are counted as unresolved.
- [ ] Tests edges: a test file imports a module, or a test function calls a symbol.
- [ ] `storage/graph.py`: `load_graph(snapshot_id, level)`, `neighbours`, `local_graph(node, hops)`, `impact_radius(node)`, `most_depended_on(n)`, `tests_for(symbol)`.
- [ ] Graph signals, written to the `signals` table:
  - import cycles (strongly connected components);
  - heavily depended-on (high fan-in) modules;
  - high fan-in modules with no test edge.
- [ ] Add the import resolution rate to the coverage report.
- [ ] Start writing benchmark questions for itsdangerous.

**Data:** `graph_edges` gains `extraction_method`, `start_line`, `end_line`, `tier` and `evidence_status`, with indexes on `tier` and `snapshot_id`. The `signals` table from M7 is created here.

**Done when:** the itsdangerous import graph matches a hand check of 5 modules, and no name-only edge is labelled confirmed.

**Tests:** relative imports, `src/` layout, alias imports, a name-only match marked inferred, test linking, cycle detection.

### Gate A: evaluator spike (week 2, alongside M3)

**Goal:** decide Jev's role and the status thresholds before Ask is built.

**Tasks**
- [ ] `intelligence/evaluators/base.py`: the `Judgment` model and `Evaluator` protocol ([section 6](#6-evaluator-contract-and-status-rule)).
- [ ] A minimal `JevEvaluator` built from the pasted Jev docs, and an `LlmJudgeEvaluator` that fills the same `Judgment`.
- [ ] 40 hand-labelled cases from 3 small repositories, two of them your own:

| Kind | Cases |
|---|---|
| Evidence supports the claim | 20 |
| Evidence does not support it | 10 |
| Looks related but does not support it | 5 |
| Genuinely ambiguous | 5 |

- [ ] Run Jev 1.13.0, Jev 1.14.0 (standard context, no long-context mode) and the LLM judge on all 40.
- [ ] Measure accuracy per field, Brier score, latency and cost.
- [ ] Write `docs/decisions/gate-a.md` with the results, the chosen evaluator and the calibrated thresholds.

**Decision rule**

| Result | Decision |
|---|---|
| Jev is clearly better, or equal at lower cost and latency | Jev is the default evaluator |
| Jev is good only on simple cases | LLM judge for support; Jev kept as an optional pre-filter |
| Jev is no better | LLM judge by default; Jev stays as an optional adapter; the result is reported |

**If behind schedule:** Gate A may slip into week 3, but it must finish before M6 starts.

### M4: Obsidian-style graph page (week 3)

**Goal:** explore the repository the way Obsidian's graph view works. The graph is the first thing users see once analysis finishes.

**Spec:** frozen in the sheet [EvidenceLoop M4/M5 Graph Spec](https://claude.ai/artifact/1dGb8QLvVbBKQEeUvLP4sS). Rows S01–S25 are the decisions and T01–T10 the tests. The IDs below refer to it.

**Day 1 spike (S03), before anything else**
- [ ] On a throwaway page, render itsdangerous with `st.echarts_chart(..., on_select="rerun")`, a stable `key`, and an options dict that stays the same across reruns. The note panel sits outside the chart.
- [ ] Confirm that clicking a node does not reset zoom or positions. If it does, fall back to a click callback or the streamlit-extras sigma graph (WebGL).

**Must**

| Area | Decision |
|---|---|
| Landing (S04) | When analysis finishes, the Home progress box ends on "Graph ready" and switches to the Graph page. |
| Which files are nodes (S10) | Source and config files are nodes; unsupported or skipped ones are muted. Assets and binaries are counted per folder in the note panel ("38 other files"), not drawn. |
| Orphans (S11) | Shown by default only below the size threshold. |
| Scaling (S14, S15) | Above 400 file nodes **or** 1,500 visible edges, the graph starts at folder level, and one click expands a folder. Both limits live in `core/config.py`. |
| Folder fan-in (S12) | The number of distinct files outside the folder that depend on anything inside it. Never a sum of file fan-ins. |
| Layout (S13) | Large graphs use seeded positions computed in Python and ECharts `layout: "none"`, so they open the same way every time. Small graphs run a live force layout from the same seeded folder clusters. |
| Drawing | Node size = fan-in. Colour = top-level folder from the theme palette. Red ring = file in an import cycle (S17). Confirmed edges solid, inferred dashed, with a toggle per edge type (S09). |
| Hover (S05) | `emphasis.focus: "adjacency"`: a file's connections light up and the rest dims. |
| Click (S06) | The note panel: symbols with line ranges, imports and imported-by as buttons that refocus, coverage, findings, evidence and code view. |
| Selection (S02) | `selectedMode` on the series. `event.selection.selected` indices map to stable node IDs through the exact order the nodes were rendered in. The selection is kept in session state and the URL. |
| Views and search (S07, S08) | Global view, or a local view of 1 to 3 hops. Search matches the full relative path. |
| Impact (S01) | Walks **reverse** dependencies (NetworkX): selecting `user_service.py` lights up `auth.py` and `app.py`. |

**Should:** recursive expansion into subfolders (S16), and a path finder for "how does A reach B?" (S20). The amber ring and size overlays move to M7 (S18, S19).

**Code:** `app/graph_view.py` holds pure functions: `select_nodes`, `aggregate_to_folders`, `seed_positions`, `build_graph_spec` (returns the options and the ordered list of IDs), and `index_to_id`. `app/app_pages/graph.py` replaces the Module map placeholder.

**Done when:** itsdangerous renders readably, clicking a node opens its note without resetting the view, and the local graph, search and impact all work.

**Tests:**
- T01: several relationship types between the same two files.
- T02: a folder with both internal and external dependencies.
- T03: orphan visibility follows the threshold rule.
- T05: impact goes in the reverse direction.
- T06: a repo with more than 400 files, and a folder with many files.
- T09: a click doesn't reset zoom.
- T10: folder fan-in counts distinct files, with no double counting.
- Plus: AppTest renders the page.

### M5: Obsidian vault export (week 3)

**Goal:** the same knowledge, opened in real Obsidian.

**Tasks**
- [ ] `obsidian/exporter.py`: `export_vault(snapshot_id)` returns a ZIP built in memory, reading only from the database.
- [ ] Layout under a repository-and-short-SHA folder: `00 Index.md`, `Coverage.md`, `files/`, `modules/`, `findings/`, `answers/`, and `.obsidian/graph.json` with colour groups per folder and tag groups for findings and answers.
- [ ] Notes carry frontmatter and wikilinks. Edges show their tier.
- [ ] One note per file under `files/` (S21), plus module, finding and coverage notes. This supersedes the old v1.0 rule of module notes only, capped at 300.
- [ ] No notes for binaries or assets (S22).
- [ ] One exporter reads the same node and edge IDs as the app graph (S23).
- [ ] `graph.json` colour groups use `path:` queries that match the vault folders (S24), with each colour stored as `{"a": 1, "rgb": <int>}` (S25).
- [ ] Export page with a file-tree preview and a download button. CLI `export` command.

**Done when:** the vault opens in Obsidian with a graph that matches the app, and every link resolves.

**Tests:**
- Link targets exist and frontmatter fields are present.
- `graph.json` is valid, and names are Windows-safe.
- T07: exported file-node and relationship IDs match the app graph.
- T08: Obsidian colours match the app palette.

> **Demo checkpoint, December 7:** explore any repository as a graph and export the vault.

### M6: Ask (week 4)

**Goal:** precise, checkable answers to questions about the repository.

**Tasks**
- [ ] Providers in `intelligence/providers/`:
  - `base.py`: `complete_structured(system, messages, schema)`, returning parsed output and usage.
  - `claude.py`: the official `anthropic` SDK, with structured output, prompt caching on the fixed system prompt, and typed errors.
  - `openai_compat.py`: ported from the prototype.
  - `fake.py`: for tests.
  - Check thinking, effort and any fallback options against Anthropic's current docs before coding.
- [ ] `core/budget.py`: a `RunBudget` checked before each LLM call. Exceeding it stops the run and records why.
- [ ] `retrieval/router.py`: lookup or analytical, by pattern rules, no LLM.
- [ ] `retrieval/search.py`: full-text search over files and symbols.
- [ ] `retrieval/evidence.py`: the evidence package.
  - Contents: full-text hits, graph neighbours (callers, callees, tests) and signals.
  - Each item has an ID, path, line range and SHA-256 of its text.
  - Capped at `EL_MAX_EVIDENCE_ITEMS`. Secrets redacted.
- [ ] `verification/verifier.py`: the cited ID exists, the path is in the snapshot, the lines are in range, and the text hash matches the file. Ported and tightened from `verify_citations`.
- [ ] `verification/status.py`: the status rule in [section 6](#6-evaluator-contract-and-status-rule).
- [ ] Harden the evaluator from Gate A: timeouts, retries that honour `Retry-After`, outage handling.
- [ ] Ask page, replacing Understand:
  - chat input and history per snapshot;
  - one evidence card per claim, with status, judgment and reason;
  - a "what was sent" panel, "show in graph", and tokens and cost.
- [ ] Answers become graph nodes and vault notes.

**Data**
- `answers(id, snapshot_id, question, route, provider, model, created_at, tokens_in, tokens_out, cost_usd)`
- `claims(id, answer_id, text, category, status, support, contradiction, sufficiency, evaluator, evaluator_version, reason)`
- `claim_evidence(claim_id, evidence_id, path, start_line, end_line, sha256)`
- `provider_send_log(id, request_id, provider, model, snapshot_id, purpose, evidence_ids, created_at)`
- `runs(id, kind, snapshot_id, tokens, cost_usd, stopped_reason)`

**Done when:** lookup questions answer with no LLM call, an analytical question returns verified claims with correct citations, and a fabricated citation is rejected.

**Tests:** router cases, evidence hashing, every verifier failure mode, the full pipeline with `FakeLLM` and a mocked Jev transport, evaluator outage, budget stop, a prompt-injection fixture repository.

### M7: signals and Review (week 5)

**Goal:** evidence-backed findings and recommendations.

**Tasks**
- [ ] `analysis/scanners/`:
  - Radon and Bandit, used as libraries.
  - OSV: from requirements files, `pyproject.toml` and `package.json`, through the batch API, sending names and versions only.
  - Gitleaks: a subprocess when installed, otherwise "not run". Secrets are stored redacted.
- [ ] `analysis/scanners/history.py`: hotspots (churn × complexity) from the last `EL_HISTORY_COMMITS` commits, through the GitHub API. It needs `EL_GITHUB_TOKEN`, and is reported as "not run" for ZIPs or without a token.
- [ ] `findings/review.py`:
  - Per module, the evidence package holds code, signals, graph context and test links.
  - The LLM returns findings with: type (`mistake`, `risk`, `improvement`, `missing-test`), title, location, evidence IDs, impact, suggested change, alternative, regression risk, tests to write.
  - Signals rank which modules are reviewed first.
- [ ] Findings pass the same verifier and evaluator. Unverified findings are labelled "possible".
- [ ] Investigate page: a signals table with filters and tool versions.
- [ ] Review page (renamed from Improve): review a module or the repository, with findings grouped by type and severity.
- [ ] Findings become graph nodes and vault notes. No overall score.
- [ ] Graph overlays:
  - An amber ring on high fan-in files with no test edge, shown only when test analysis completed (S18, test T04). A missing test edge after incomplete analysis is not proof that there are no tests.
  - Node size by complexity or churn, alongside in-degree (S19).

**Data**
- `signals(id, snapshot_id, tool, tool_version, rule, path, start_line, end_line, severity, message, raw_json)`
- `findings(id, snapshot_id, run_id, type, title, status, severity, impact, suggested_change, alternative, regression_risk, tests_to_write, plus the judgment fields)`
- `finding_evidence`, linked like `claim_evidence`

**Done when:** a review of itsdangerous yields findings that each cite valid lines, and scanner output shows tool versions.

**Tests:** scanner parsers on fixture output, finding verification, OSV and GitHub calls mocked, history marked not run without a token.

**Kill check:** label the top 10 findings on two repositories. If fewer than 50% are valid and actionable, the narrow criterion in the PRD applies.

> **Demo-ready checkpoint, December 21:** ingest, graph, ask, review, export.

### M8: benchmark and evaluator decision (week 6)

**Goal:** prove the answers and findings are reliable, and show what they're compared against.

**Dataset:** itsdangerous plus 4 small and medium Python projects, with about 20 labelled questions each, in YAML with the expected files and lines. Questions are written from week 2 onward.

**Configurations**

| Run | Setup |
|---|---|
| A0 | Plain LLM given the repository text up to its context limit, asked to cite file and lines. No pipeline. Scored by the same verifier. |
| A | Full-text evidence, LLM, verifier |
| B | A plus graph neighbours |
| C-Jev | B plus the Jev evaluator (version from Gate A) |
| C-LLM | B plus the LLM judge evaluator |
| D | The better C plus signals: the full system |

**Review benchmark:** the top 10 findings per repository from A0 and from D, shuffled and labelled blind as valid or invalid, and actionable or not.

**Metrics:** answer correctness, citation precision and recall, invalid citation rate, unsupported claims among verified ones, retrieval recall at k, review top-10 precision, tokens, cost and latency.

**Output:** a report page and a Markdown report. Keep Jev in the default pipeline only if C-Jev measurably beats C-LLM, or matches it at lower cost.

**Labelling load:** about 100 questions and 100 findings. Budget 15 to 25 hours across weeks 2 to 6.

### M9: packaging and release (week 7)

- [ ] FastAPI endpoints over the service layer: ingest, snapshot, coverage, analyze, ask, review, export.
- [ ] Dockerfile and full CLI.
- [ ] Static gallery: 3 to 5 pre-analyzed repositories as report pages and vault downloads, with no live questions.
- [ ] README with benchmark results, including the A0 comparison, and a short demo video.
- [ ] Final docs pass. End-to-end smoke test in CI with `FakeLLM`.

**Done when:** setup works on a fresh machine from the README alone.

## 6. Evaluator contract and status rule

```python
class Judgment(BaseModel):
    support: float        # evidence shows the claim
    contradiction: float  # evidence shows the opposite
    sufficiency: float    # evidence is enough to decide
    evaluator: str        # "jev" or "llm_judge"
    model_version: str
    reason: str | None

class Evaluator(Protocol):
    def judge(self, claim: str, evidence: list[EvidenceItem]) -> Judgment: ...
```

**Status rule, applied in order:**
1. Any deterministic verifier check fails → **rejected**, with the failed check as the reason.
2. Evaluator unavailable → **insufficient_evidence**, reason "evaluator unavailable". Never auto-verified.
3. Contradiction at or above its threshold → **rejected**, reason "contradicted by evidence".
4. Support and sufficiency both at or above their thresholds → **verified**.
5. Otherwise → **insufficient_evidence**.

Thresholds stay marked "uncalibrated" in the UI until Gate A sets them.

## 7. Timeline

| Week | Dates | Work | Checkpoint |
|---|---|---|---|
| 1 | Nov 17 to 23 | Commit UI, Step 0, Step 0b, M2 | Analysis on itsdangerous |
| 2 | Nov 24 to 30 | M3, Gate A, start benchmark questions | Gate A decision written |
| 3 | Dec 1 to 7 | M4, M5 | Demo: graph and vault |
| 4 | Dec 8 to 14 | M6 | Verified answers |
| 5 | Dec 15 to 21 | M7, kill check | Demo-ready full loop |
| 6 | Dec 22 to 28 | M8 | Benchmark report |
| 7 | Dec 29 to Jan 4 | M9, buffer | V1 release |

**Cut order when behind:** JS/TS depth, module level in the graph, history hotspots, answers as graph nodes, FastAPI, gallery, visual polish.
**Never cut:** the verifier, the coverage ledger, the benchmark or the A0 baseline.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Dynamic Python makes call edges wrong | Confirmed and inferred tiers. Unresolved calls get no edge. The UI shows the tier. |
| The LLM cites lines that don't support the claim | Deterministic verifier and evaluator. Unverified claims are never stated as fact. |
| The Jev API differs from expectations or is unavailable | Adapter built to the real docs in week 2. Fails safe. Gate A decides its role. |
| Large repositories make an unreadable graph | Folder level with click-to-expand above 400 file nodes or 1,500 visible edges, a seeded fixed layout, and assets counted instead of drawn. |
| Each click reruns the script and resets the graph's zoom | Day 1 spike in M4 with a stable key and options. Fall back to a click callback or the sigma graph. |
| LLM cost and latency | Lookups skip the LLM. Prompt caching. Evidence cap. Per-run budget. A cheaper model for Ask. |
| Secrets or prompt injection in repositories | Gitleaks redaction before sending. Repository text treated as data. Injection fixture tests. |
| GitHub rate limits | Token support. History hotspots capped and optional. |
| Labelling takes longer than planned | Questions written from week 2. The benchmark stays at 5 repositories if needed. |
| A plain LLM is nearly as good as the pipeline | A0 measured explicitly. Narrow criterion in the PRD. |

## 9. Inputs needed

| Input | Needed by |
|---|---|
| Jev API docs and key (endpoint, request and response, authentication, versions) | November 24, for Gate A |
| Anthropic API key for live runs (tests run offline without it) | M6 live checks |
| GitHub token (optional), for rate limits and history hotspots | M7 |
| Approval to commit the current UI and docs work | Before M2 |
| Four benchmark repositories to join itsdangerous | End of week 2 |

## 10. Definition of done for V1

- [ ] Five benchmark repositories analyzed end to end, each with a coverage report.
- [ ] Zero invalid citations in claims or findings shown as verified.
- [ ] Review top-10 precision and the A0 comparison measured and published in the README.
- [ ] Evaluator decision recorded with data.
- [ ] Vault opens in Obsidian with all links resolving.
- [ ] Gallery of at least 3 pre-analyzed repositories.
- [ ] Fresh-machine setup works from the README alone.
