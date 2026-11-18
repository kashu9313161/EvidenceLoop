# EvidenceLoop: project guide

> **Ask a codebase questions and get answers you can check.**
> Every claim cites the exact file and lines it rests on, and says how sure we are.

This guide covers what EvidenceLoop is, how it works and how to run it, including the small details.
For the build order and milestone checklists, see [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

**Contents**

1. [The problem](#1-the-problem)
2. [What makes EvidenceLoop different](#2-what-makes-evidenceloop-different)
3. [How it works, end to end](#3-how-it-works-end-to-end)
4. [Key concepts (glossary)](#4-key-concepts-glossary)
5. [The Obsidian graph](#5-the-obsidian-graph)
6. [Asking questions](#6-asking-questions)
7. [Review mode: mistakes and improvements](#7-review-mode-mistakes-and-improvements)
8. [Security and privacy](#8-security-and-privacy)
9. [Tech stack](#9-tech-stack)
10. [Configuration](#10-configuration)
11. [How to run it](#11-how-to-run-it)
12. [Project status](#12-project-status)
13. [What EvidenceLoop is not](#13-what-evidenceloop-is-not)
14. [FAQ](#14-faq)

---

## 1. The problem

When you join a team or pick up an unfamiliar repository, you have to answer questions like these:

- Where is the authentication logic?
- Which modules depend on the payment service?
- What could break if I change this function?
- Are there security problems? Which parts have no tests?
- What should we refactor first?

Answering them today has three problems:

| Problem | What it looks like |
|---|---|
| **It's slow** | You open files, grep, trace imports and read history by hand. |
| **Tools are disconnected** | A scanner flags a function, but you still have to work out who calls it and whether it's tested. |
| **AI answers aren't checkable** | A chatbot says "the auth module doesn't validate permissions." Which file? Which lines? Is it even true? |

## 2. What makes EvidenceLoop different

EvidenceLoop doesn't just tell you what it thinks. It shows the evidence, where it comes from and how reliable it is.

1. **Evidence-grounded claims.** Every claim cites `file:start–end` in one exact snapshot of the code, and is checked before you see it.
2. **Honest coverage.** It always shows what it did **not** analyze. "No issues found" and "not analyzed" are never confused.
3. **A relationship graph.** Imports, calls, inheritance and tests are connected, and you can explore them like Obsidian's graph view.
4. **Measured reliability.** Answers are benchmarked against repositories with known answers.

There is deliberately **no single "health score"**. A number like "87% healthy" hides the fact that a repo can have good tests and weak security at the same time. EvidenceLoop shows the separate measurements instead.

## 3. How it works, end to end

```
 GitHub URL or ZIP
        │
        ▼
 ┌──────────────┐   validate URL, resolve the commit SHA,
 │  1. Ingest   │   download, check the ZIP safely, extract atomically
 └──────┬───────┘
        ▼
 ┌──────────────┐   immutable copy of the files, identified by
 │  2. Snapshot │   github:owner/repo@<commit_sha>
 └──────┬───────┘
        ▼
 ┌──────────────┐   Python syntax tree: classes, functions, imports, calls
 │  3. Analyze  │   JS/TS: imports and exports (shallow)
 │              │   scanners: Radon, Bandit, OSV, Gitleaks
 └──────┬───────┘
        ▼
 ┌──────────────┐   SQLite: full-text index (FTS5), graph nodes and edges,
 │ 4. Knowledge │   coverage ledger, signals
 └──────┬───────┘
        ▼
 ┌──────────────┐   "where is X?" is answered by direct lookup, no LLM
 │ 5. Ask or    │   anything else goes to the LLM with an evidence package
 │    review    │
 └──────┬───────┘
        ▼
 ┌──────────────┐   deterministic checks (path, lines, text hash), then
 │  6. Verify   │   evaluator judges support → verified / insufficient / rejected
 └──────┬───────┘
        ▼
 ┌──────────────┐   Obsidian-style graph, evidence cards,
 │   7. Show    │   findings, Obsidian vault export
 └──────────────┘
```

The LLM is **not** the whole system. The ingestion, parsing, index, graph, scanners and verifier produce the evidence. The LLM only helps interpret that evidence and explain it.

## 4. Key concepts (glossary)

| Term | Meaning |
|---|---|
| **Snapshot** | One immutable copy of a repository at one moment. Every result belongs to exactly one snapshot, so yesterday's finding never gets attached to today's code. |
| **Commit SHA** | The 40-character Git ID of the exact version analyzed, such as `672971d6…`. A GitHub snapshot ID is `github:{owner}/{repo}@{sha}`. ZIP snapshots use `zip-{first 24 chars of the archive's SHA-256}`. |
| **Coverage ledger** | A record of every file and what happened to it in each analysis stage. **pending**: eligible, waiting for analysis. **skipped**: won't be analyzed, with the reason (unsupported type, ignored folder, symlink). **analyzed**: done. **failed**: the analyzer couldn't process it, with the reason. |
| **Syntax tree (AST)** | Python's own structural parse of the source. It tells a real function call apart from the same word in a comment or string. |
| **Graph node** | A thing in the code: a file, module, class or function. Later also a finding or a saved answer. |
| **Graph edge** | A relationship between nodes: `imports`, `defines`, `calls`, `inherits`, `tests`. |
| **Confirmed edge** | A relationship proven statically, for example a module imports a name and then calls it. |
| **Inferred edge** | A likely relationship, matched by name only. It's shown dashed and is **never** treated as proof. |
| **Evidence package** | What gets sent to the LLM for one question: numbered items, each with a path, line range and a SHA-256 hash of its text. |
| **Claim** | One statement in an answer. It must cite evidence items. |
| **Evaluator** | The independent judge of whether cited evidence supports a claim: **Jev** or an **LLM judge**. Gate A decides which is the default. |
| **Judgment** | What the evaluator returns for each claim: three 0–1 numbers. **Support**: the evidence shows the claim. **Contradiction**: the evidence shows the opposite. **Sufficiency**: the evidence is enough to decide. |
| **Verified** | Every deterministic check passed, **and** both support and sufficiency are at or above their thresholds. Only verified claims are stated as fact. |
| **Insufficient evidence** | The citations are real, but the evidence doesn't clearly support the claim, or the evaluator was unavailable. The claim is shown as unconfirmed. |
| **Rejected** | A deterministic check failed (wrong file, lines out of range, changed text, evidence that wasn't in the package), **or** the evidence contradicts the claim. The claim isn't shown as fact. |
| **Gate A** | The week-2 experiment that compares Jev 1.13.0, Jev 1.14.0 and an LLM judge on 40 hand-labelled cases. It picks the default evaluator and sets the thresholds. |
| **A0 baseline** | A plain LLM given the repository text with no pipeline. The benchmark compares EvidenceLoop against it, to show the pipeline is worth having. |
| **Run budget** | A hard cap on tokens and cost for one Ask or Review run. Going over it stops the run and records why. |
| **Signal** | Raw output from a scanner (Radon, Bandit, OSV, Gitleaks), stored with its tool and version. A signal is an observation, not a verdict. |
| **Finding** | A review result with one of these types: **mistake**, **risk**, **improvement**, **missing-test**. It comes with evidence and goes through the same verification as claims. |
| **Provider send log** | A record of every request to an external LLM: which provider and model, which snapshot, which evidence items were sent, and why. |

## 5. The Obsidian graph

The graph is the centre of EvidenceLoop. It works like Obsidian's graph view, but every dot and line is backed by code evidence. When analysis finishes, the app opens the graph straight away, drawing every file and every connection between files at once.

The detailed decisions are frozen in the [M4/M5 Graph Spec sheet](https://claude.ai/artifact/1dGb8QLvVbBKQEeUvLP4sS).

**Reading the graph**

| Visual | Meaning |
|---|---|
| Dot | A source or config file (or a folder, in big repos). Classes and functions appear in the note panel. |
| Muted dot | A file EvidenceLoop skipped or can't analyze yet |
| Dot size | How many other files depend on it. Bigger means a change there has a wider impact. For a folder: how many distinct files outside it depend on something inside it. |
| Dot colour | Top-level folder, such as `src/`, `tests/` or `docs/`. |
| Red ring | The file is part of an import cycle |
| Amber ring (from M7) | Many files depend on it, test analysis finished, and no test covers it |
| Solid line | Confirmed relationship |
| Dashed line | Inferred relationship (name match only) |

Images, lockfiles and other binaries aren't drawn, so they don't bury the code. The folder's note counts them instead ("38 other files").

**Using it**

- **Hover a dot** to light up everything it connects to, while the rest fades. This is the quickest way to see one file's connections to all the others.
- **Global graph:** the whole repo.
  - Up to 400 files and 1,500 lines, you see every file at once.
  - Bigger repos start with folders as dots; click a folder to expand it.
  - Big graphs open with the same layout every time.
- **Local graph:** one selected file plus 1–3 hops around it.
- **Search** by full path, such as `src/auth/user_service.py`, to focus a file.
- **Toggles** show or hide each kind of line, tests, and orphans (files with no links). Orphans are on by default only in small repos.
- **Impact** highlights every file that depends on the selected one, directly or indirectly. In other words, what could break if you change it.
- **Click a dot** to open its **note**:
  - symbols with line ranges;
  - imports and imported-by (click to jump);
  - coverage status;
  - linked findings and answers;
  - the code itself.

  Clicking keeps your zoom and position.
- Saved answers and findings become dots too, linked to the files they cite.
- The current view is in the URL (`?snapshot=…&node=…`), so you can share it.

**Opening it in real Obsidian.** The Export page downloads a vault ZIP named `<repo>@<sha7>`:

```
itsdangerous@672971d/
├── 00 Index.md            snapshot identity, coverage, most depended-on modules
├── files/…                one note per source or config file: symbols, [[imports]], [[imported by]]
├── modules/…              one note per module
├── findings/…             review findings with evidence links
├── answers/…              saved Q&A with verification status
├── Coverage.md            what was analyzed, skipped or failed
└── .obsidian/graph.json   folder colour groups matching the app, so the graph looks right on first open
```

Unzip it, open the folder as a vault in Obsidian, and press the graph view button. You get the same graph as the app, as linked notes you keep. The vault uses the same file and link IDs as the app, and binaries get no notes. It's a snapshot export, not a live sync.

*(An older draft allowed module notes only, capped at 300. That rule is superseded: there is one note per file.)*

## 6. Asking questions

There are two kinds of question, handled differently.

**Lookup questions** ("Where is the login endpoint?", "Who calls `verify_signature`?") are answered directly from the index and the graph. **No LLM is called**, so they're fast, free and exact.

**Analytical questions** ("Why is this module hard to change?", "How does signing work?") go through the full loop:

1. **Retrieve:** full-text matches, their graph neighbours (callers, callees, tests) and any scanner signals.
2. **Package:** each piece of evidence gets an ID, path, line range and text hash. Secrets are redacted first.
3. **Generate:** the LLM (Claude by default) writes claims, and each claim must cite evidence IDs and lines.
4. **Verify deterministically:** the file is in the snapshot, the lines exist, the text hash matches, and the cited item was actually in the package. Any failure means **rejected**.
5. **Judge with the evaluator** (Jev or an LLM judge): it returns support, contradiction and sufficiency. Contradicted means **rejected**. Supported and sufficient means **verified**. Anything else is **insufficient evidence**.
6. **Show:** one card per claim.

Each run has a token and cost budget. If a run hits it, the run stops and says so rather than overspending.

**Reading an answer card**

```
┌──────────────────────────────────────────────────────────────┐
│ ✔ Verified   Signer.get_signature() derives the key on each  │
│              call; the derived key is not cached              │
│   support 0.91 · contradiction 0.02 · sufficiency 0.88        │
│   ▸ src/itsdangerous/signer.py:180–196   (expand for code)    │
└──────────────────────────────────────────────────────────────┘
```

*(Illustrative. Real cards come from the repository you analyze.)*

Under each answer, a "What was sent" panel lists exactly which evidence went to the LLM, along with token and cost counts. "Show in graph" highlights the cited files on the graph.

**The rule:** deterministic code finds facts and decides status. The LLM explains and proposes. The evaluator judges one narrow question: does this evidence support this claim? **The LLM never grades its own work.**

## 7. Review mode: mistakes and improvements

Review looks at a module, or the whole repository, and reports:

| Type | Example |
|---|---|
| **mistake** | An exception is swallowed with a bare `except:` (lines 88–91). |
| **risk** | `subprocess` is called with `shell=True` on a value built from input (Bandit B602). |
| **improvement** | A function has cyclomatic complexity 14 (Radon), so it could be split. |
| **missing-test** | No test imports or calls `Serializer.loads` (graph `tests` edges). |

Every finding includes **location, evidence, potential impact, suggested change, one alternative, regression risk** (what could break) and **tests to write before changing it**.

Findings come from signals plus an LLM review of the code and its graph context. Signals include scanner output, graph checks (import cycles, heavily depended-on modules with no tests) and, for GitHub repos with a token, commit-history hotspots (files that change often and are complex). Signals also decide which modules get reviewed first. Findings go through the **same verifier and evaluator** as answers. Unverified findings are labelled **"possible"** and are never stated as fact.

**Evidence, not verdicts.** "Function X has complexity 14 and no test covers it (lines 40–92)" is something you can check and act on. "Your code is bad" isn't, so EvidenceLoop never says it.

## 8. Security and privacy

Every repository is treated as **untrusted input**.

**Nothing from the repository is ever executed.** EvidenceLoop never runs `pip install`, `setup.py`, tests or scripts. All analysis is static.

**ZIP safety.** Every entry is validated *before* anything is written to disk:

| Check | Limit (`core/config.py`) |
|---|---|
| Archive download or upload size | 100 MB (`MAX_ARCHIVE_DOWNLOAD_BYTES`) |
| Total uncompressed size of extracted files | 250 MB (`MAX_TOTAL_UNCOMPRESSED_BYTES`) |
| Single extracted file | 10 MB (`MAX_FILE_UNCOMPRESSED_BYTES`) |
| Number of files | 5,000 (`MAX_FILES`) |
| Compression ratio (zip-bomb guard) | 100× (`MAX_COMPRESSION_RATIO`) |
| Path traversal, absolute paths, Windows device names (`CON`, `NUL`…), colons, trailing dots | rejected |
| Encrypted entries, corrupt entries, duplicate or case-only-duplicate paths | rejected |
| Symlinks and special files | recorded as skipped, never created |

Extraction writes to a temporary folder and is published with one atomic rename, so a failure never leaves a half-extracted snapshot. Decompressed bytes are counted as they stream, so an archive whose headers lie about sizes still can't exceed the limits.

**Data sent to the LLM.**

- Only the evidence items for the current question are sent, never the whole repository.
- Secrets found by Gitleaks are redacted before sending.
- Every request is recorded in the provider send log, and you can see it under each answer.
- Repository text is treated as data, not instructions. If a comment in the repo tries to steer the model, the verifier still rejects any claim that isn't backed by evidence.

**Credentials** (LLM and Jev keys) come only from environment variables. They are never stored in the database, logs or exports.

## 9. Tech stack

| Technology | Used for | Why |
|---|---|---|
| Python 3.11+ | Everything | One language end to end |
| SQLite + FTS5 | Storage and full-text search | Local, no server, built-in search |
| NetworkX | Graph traversal | In-memory graph built from SQLite rows |
| Python `ast` | Code structure | Exact, standard-library parser |
| Streamlit | The app UI | Fast to build, native charts, light and dark themes |
| ECharts (via `st.echarts_chart`) | Obsidian-style graph | Force layout, zoom and drag, click-to-select |
| Anthropic SDK (Claude) | Default LLM: Sonnet for Ask, Opus for Review | Structured outputs; one model per task to balance cost and depth |
| OpenAI-compatible client | Optional LLM | Ollama, Groq and others |
| Jev or an LLM judge | Evaluator | Independent check of LLM claims. Gate A picks the default. |
| Radon, Bandit, OSV, Gitleaks | Signals | Complexity, security, vulnerable dependencies, secrets |
| httpx | GitHub API and downloads | Streaming with size limits, mockable in tests |
| Pydantic | Data models | Validated structures |
| FastAPI | API boundary | Same service layer as the UI and CLI |
| pytest, Ruff, GitHub Actions | Quality | Tests and lint on Ubuntu and Windows |
| Docker | Packaging | Reproducible runtime |

**Deliberately left out of V1**

| Excluded | Why |
|---|---|
| Vector databases and embeddings | We first want to show that structure plus full-text search plus the graph answer well, and to measure that. |
| PostgreSQL, Redis, Celery | No server infrastructure is needed for a local tool. |
| React | Streamlit is enough for V1. |
| Running repository code | Security and reproducibility risks |
| Private repositories | Outside V1 scope |
| Deep analysis of Java or other languages | Python is analyzed deeply. JS/TS gets a shallow pass. |
| Live Obsidian sync | A snapshot export is enough. |

## 10. Configuration

All settings are environment variables. Nothing secret goes in files.

| Variable | Default | Purpose |
|---|---|---|
| `EL_LLM_PROVIDER` | `claude` | `claude`, `openai_compat` or `fake` (tests) |
| `ANTHROPIC_API_KEY` | — | Claude key. An `ant auth login` profile also works. |
| `EL_MODEL_ASK` | `claude-sonnet-5-5` | Model for Ask |
| `EL_MODEL_REVIEW` | `claude-opus-5-5` | Model for Review |
| `EL_OPENAI_BASE_URL`, `EL_OPENAI_API_KEY` | — | OpenAI-compatible endpoint (such as `http://localhost:11434/v1` for Ollama) and its key, if it needs one |
| `EL_EVALUATOR` | set by Gate A | `jev` or `llm_judge` |
| `EL_JEV_URL`, `EL_JEV_API_KEY` | — | Jev endpoint and key |
| `EL_JEV_VERSION` | set by Gate A | `1.13.0` or `1.14.0`, pinned |
| `EL_T_SUPPORT`, `EL_T_SUFFICIENCY`, `EL_T_CONTRADICTION` | 0.80, 0.70, 0.50 | Status thresholds. Marked "uncalibrated" until Gate A sets them. |
| `EL_MAX_EVIDENCE_ITEMS` | `12` | Cap on evidence items per question |
| `EL_MAX_RUN_TOKENS`, `EL_MAX_RUN_COST_USD` | 200000, 1.00 | Per-run budget |
| `EL_GITHUB_TOKEN` | — | Optional: higher GitHub rate limits and history hotspots |
| `EL_HISTORY_COMMITS` | `100` | Commits scanned for hotspots |

Without an LLM key, everything except Ask and Review still works: ingestion, analysis, the graph, coverage and export.

## 11. How to run it

```bash
# 1. Install (from the repository root)
python -m venv .venv
.venv\Scripts\activate            # Windows. On macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

# 2. Ingest a repository
python -m cli.main ingest https://github.com/pallets/itsdangerous
python -m cli.main ingest path\to\repo.zip

# 3. Inspect it
python -m cli.main snapshot "github:pallets/itsdangerous@<sha>"
python -m cli.main coverage "github:pallets/itsdangerous@<sha>"

# 4. Open the app
streamlit run app/streamlit_app.py

# 5. Run the checks
ruff check .
pytest -q
```

Planned commands (see the implementation plan): `analyze <snapshot_id>`, `ask <snapshot_id> "question"` and `export <snapshot_id> --out vault.zip`.

**Light and dark mode.** The app has both themes and follows your operating system by default. To switch, open the **⋮ menu (top right) → Settings → Light / Dark / System**.

## 12. Project status

| Area | Status |
|---|---|
| Ingestion: GitHub (commit SHA), ZIP upload, secure extraction, snapshots, coverage ledger | ✅ Done |
| Ingestion hardening: two-phase validation, atomic extraction, security test suite, CI | ✅ Done |
| App: Add repository, Snapshot overview, Coverage ledger, snapshot kept in the URL | ✅ Done |
| Light and dark themes | ✅ Done |
| Python syntax-tree analysis, JS/TS shallow analysis | 🔜 Milestone 2 |
| Knowledge graph with confirmed and inferred edges, plus graph signals | 🔜 Milestone 3 (week 2) |
| Gate A: choose the evaluator (Jev or LLM judge) and calibrate thresholds | 🔜 Week 2, alongside M3 |
| Obsidian-style graph page | 🔜 Milestone 4 (demo Dec 7) |
| Obsidian vault export | 🔜 Milestone 5 (demo Dec 7) |
| Ask (LLM + evaluator, verified claims, run budgets) | 🔜 Milestone 6 |
| Signals, history hotspots and Review (mistakes and improvements) | 🔜 Milestone 7 (demo-ready Dec 21) |
| Benchmark, including the plain-LLM baseline (A0) | 🔜 Milestone 8 |
| API, Docker, gallery, V1 release | 🔜 Milestone 9 (Jan 4) |

Details, tasks and acceptance criteria: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

## 13. What EvidenceLoop is not

- Not an autonomous agent that edits your repository
- Not a replacement for your IDE
- Not something that runs uploaded code
- Not a runtime debugger. Static analysis can't prove what happens at runtime, and EvidenceLoop says so when that matters.
- Not a universal multi-language analyzer. V1 is deep for Python and shallow for JS/TS.
- Not a hosted service for private repositories
- No guarantee that a reported issue is exploitable. Scanner output is an observation, not a confirmed vulnerability.

## 14. FAQ

**Why not use embeddings or a vector database?**
V1 tests whether exact structure (syntax tree and graph) plus full-text search answers engineering questions well, and measures it. Embeddings add infrastructure and make results harder to explain. If the benchmark shows they would help, they can come later.

**Why does a claim say "insufficient evidence"?**
Its citations point to real code, but that code doesn't clearly support the statement, or the evaluator couldn't be reached. EvidenceLoop shows the uncertainty rather than guessing.

**Does it run my code?**
No. Never. It doesn't install dependencies or run scripts or tests. Everything is static analysis of the files.

**What gets sent to Claude (or another LLM)?**
Only the numbered evidence snippets for your question, with secrets redacted, plus the question itself. Every request is logged, and you can see it under the answer. Ingestion, analysis, the graph and export never call an LLM.

**What is Jev for?**
Jev is one candidate for the evaluator: the independent judge of whether cited evidence supports, contradicts or is enough to decide a claim, so the LLM isn't judging its own answer. Gate A, in week 2, compares Jev 1.13.0, Jev 1.14.0 and an LLM judge on 40 hand-labelled cases. The winner becomes the default. Jev stays in the default pipeline only if it measurably beats the LLM judge, or matches it at lower cost.

**Why compare against a plain chatbot?**
The benchmark includes a baseline (A0): a plain LLM given the repository text and asked to cite files and lines, with no pipeline. If EvidenceLoop can't beat that on correctness and citation precision, the extra machinery isn't earning its keep, and the benchmark would show it.

**Why no overall health score?**
A single number hides important differences. A repo can have great tests and a vulnerable dependency. EvidenceLoop shows each measurement separately.

**Can I use it without an API key?**
Yes, for everything except Ask and Review: ingestion, coverage, analysis, the graph and the vault export.
