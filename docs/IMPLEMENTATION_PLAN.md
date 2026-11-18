# EvidenceLoop V2: plan

**Version 2.0, November 18, 2026.** Replaces the V1 plan (v1.1). For how the product works today, see
[PROJECT_GUIDE.md](PROJECT_GUIDE.md).

## 1. Why V1 was replaced

V1 had a solid core (safe ingestion, a real Python parser) but the product on top of it failed the only test that
matters: a new user could not tell what to do or what they had learned.

| V1 problem | Cause | V2 answer |
|---|---|---|
| Eight sidebar pages, six of them "coming soon" | The UI mirrored the build plan, not the user's task | One input, then one workspace per repository with four views |
| Analysis showed raw class and function tables | Parsing was treated as the product instead of the input | An understanding layer turns facts into a summary, a map, flows and issues |
| No way to see how files connect | The graph was scheduled for a later milestone | The graph is built in the same pass and is the centre of the product |
| Streamlit could not do a smooth, clickable graph | Every click reruns the whole page | React + FastAPI; the engine and storage are unchanged Python |
| Nothing was flagged as wrong | Issues depended on an LLM milestone | Deterministic rules find real problems now, with exact lines as evidence |

## 2. Product principles (unchanged in spirit, now enforced in the UI)

1. **One flow.** Paste a repository → watch it being read → land in its workspace. Nothing else to learn.
2. **Explain before listing.** Every screen opens with a sentence a person would say, then the details.
3. **Every claim is checkable.** Issues and descriptions point to files and lines at a pinned commit.
4. **Honest coverage.** The workspace says what was read, what was skipped and why, and which links are guesses.
5. **Never run the repository's code.**
6. **No placeholder screens.** A feature appears when it works.

## 3. What is built (V2.0, delivered November 18)

### Experience

| Screen | What it does |
|---|---|
| Home | One field for a GitHub URL (or drop a ZIP). Live, step-by-step progress. List of analysed repositories with a one-line summary each. |
| Overview | A plain-English headline, the README's own description, key facts, how the code is organised (areas), where it starts (entry-point flows), HTTP routes, the issues to fix first, the files that matter most, the stack, and what was analysed. |
| Map | Obsidian-style dependency graph. Size = how many files depend on it; colour = area or issue severity; red ring = import cycle; dashed = best-guess link. Hover traces a file's connections; click opens its details; "Show what depends on it" lights up everything a change would reach. Files or areas view, search, test and orphan toggles. |
| Issues | Ranked by how far each problem spreads. Each one says why it matters, what to do, how sure we are, and shows the exact code. Filter by severity and kind. |
| Files | Folder tree with issue markers, the file's role and description, its classes and functions with complexity, the code with syntax highlighting, and what uses it. |
| Search | Ctrl/⌘ K across file names, classes and functions, and full text. |
| Details panel | Opens from anywhere: what the file is for, its numbers, its issues, what uses it, what it uses, its tests, its most complex functions. |

Light and dark themes, keyboard focus, reduced motion and phone widths are supported.

### Understanding engine (`analysis/`)

| Part | Detail |
|---|---|
| Import resolution | Python: relative imports, `src/` layouts, monorepo project roots, submodule imports. JS/TS: relative paths, index files, `@/` aliases, asset imports ignored. Each link is **confirmed** (only one file fits) or **inferred** (ambiguous, or shadows a standard-library name). |
| Graph metrics | Fan-in (distinct importing files, tests excluded), impact (everything that reaches a file), PageRank importance, import cycles, test links. |
| File roles | Entry point, API, UI, script, core, model, config, package, test, build script, stub, component, with the reason. |
| Descriptions | Deterministic sentences: module docstring, what it defines, routes, who uses it, what it builds on, tests, libraries. |
| Areas and flows | Folder areas (monorepos descend automatically), cross-area dependencies, entry-point flows three levels deep, HTTP routes. |
| Stack | Frameworks and libraries from imports and hand-written manifests; Docker and CI from files. Frozen `pip freeze` lists are not mistaken for the stack. |
| Issue rules (12) | Import cycles; complex/long/deeply nested functions; large modules; god classes; mutable default arguments; swallowed and bare exceptions; wildcard imports; possible hard-coded credentials (redacted); untested hubs; no tests; unpinned and undeclared dependencies (type-checking-only imports excluded); UTF-16 manifests; unconnected modules; no CI. |
| Coverage | Files discovered, parsed, indexed, skipped (with reasons) and failed; import resolution rate; confirmed vs inferred links. |

Verified on itsdangerous, markupsafe, ML-Projects and on EvidenceLoop itself, where it found two real undeclared
dependencies and flagged the report builder as too complex (since refactored).

### Interfaces and quality

- `api/main.py`: analysis jobs with live progress, report, file view, search; serves the built web app.
- `cli/main.py`: `serve`, `analyze`, `report`, `list`, `coverage`.
- 106 tests, all offline; `ruff` clean. Moved project folders keep working (snapshot paths are re-resolved).

## 4. What comes next

Ordered by what a user gains, each shippable on its own. Dates assume 15–20 hours a week.

### M3. Ask: questions answered with evidence (Nov 19 – Dec 2)

The original EvidenceLoop idea, now on solid ground: the graph and index already exist.

1. **Gate A first (Nov 19–24).** Thin `Evaluator` interface (`judge(claim, evidence) -> support, contradiction,
   sufficiency`) with Jev and an LLM-judge implementation. 40 hand-labelled cases from the three repositories
   (20 supporting, 10 not, 5 related-but-not-supporting, 5 ambiguous). Measure accuracy, calibration, cost, latency
   on Jev 1.13 and 1.14. Write `docs/decisions/gate-a.md`. Needs: TypeSafe docs and key.
2. **Retrieval.** Router (lookups answered from the index with no LLM), evidence package from full-text hits plus
   graph neighbours plus issues, capped and secret-redacted.
3. **Answering.** Claude by default behind a provider interface; structured claims that cite evidence IDs.
4. **Verification.** Deterministic checks (cited ID exists, path in snapshot, lines in range, text hash matches),
   then the evaluator. Status: verified, insufficient evidence, or rejected. A send log of what left the machine.
5. **UI.** An "Ask" box in the top bar and a side panel with claim cards; "Show in map" highlights cited files.

Done when a lookup answers with no LLM call, an analytical question returns verified claims with correct
citations, and a fabricated citation is rejected.

### M4. Deeper facts (Dec 3 – Dec 12)

- Function-level call graph (resolved through imports; dynamic calls marked unresolved) so the details panel shows
  callers and callees, and Impact works per function.
- Real JS/TS parsing (tree-sitter) instead of regular expressions.
- Scanners behind one adapter: Bandit, OSV (dependency vulnerabilities, names and versions only), Gitleaks if
  installed. Each reported as "not run" when unavailable.
- Change hotspots from commit history via the GitHub API (needs a token; "not run" otherwise).
- Large repositories: folder aggregation with expand-in-place above ~400 files or ~1,500 links.

### M5. AI explanations on top of the facts (Dec 13 – Dec 19)

- LLM-written summaries for areas, key files and flows, generated from the deterministic facts, cached per commit,
  and shown with the facts they rest on. Clearly labelled as generated.
- "Explain this flow" and "Explain this issue in this codebase" buttons.

### M6. Obsidian vault export (Dec 20 – Dec 24)

- One exporter over the same report: a note per code file with wikilinks matching the map's links, area notes,
  issue notes, a coverage note, `graph.json` colour groups matching the app's area colours.
- Parity test: exported node and link IDs equal the app's.

### M7. Review: recommendations that hold up (Dec 26 – Jan 2)

- LLM-proposed findings seeded by the deterministic issues and graph signals, each verified like Ask claims.
- Suggested change, alternative, regression risk and tests to write; unverified findings shown as "possible".

### M8. Benchmark (Jan 3 – Jan 9)

- Five repositories, ~20 labelled questions each, plus blind-labelled top-10 findings.
- Configurations: A0 plain LLM with the repository text; A full-text; B + graph; C + evaluator (Jev or LLM judge);
  D everything. Metrics: answer correctness, citation precision/recall, unsupported claims, review precision, cost,
  latency. Results go in the README, whatever they are.

### M9. Package and show (Jan 10 – Jan 16)

- Docker image; `evidenceloop serve` as the one command.
- Static gallery: export the workspace for 3–5 public repositories as a site anyone can browse without installing.
- Demo video.

## 5. Kill and narrow criteria

- If review top-10 precision is below 50% on two repositories, ship V2 as an understanding tool (Overview, Map,
  Issues, Files, Ask) and drop Review.
- If the plain-LLM baseline matches Ask on correctness and citations, lead with what it can't do: the map, impact,
  coverage and deterministic issues.
- If Jev doesn't beat the LLM judge in Gate A, keep it as an optional evaluator and report the result.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Dynamic Python makes links wrong | Confirmed vs inferred tiers shown in the map; unresolved imports counted in coverage |
| Rule-based issues feel generic | Ranked by reach in the graph; every issue cites its lines; rules tuned on real repositories |
| Large repositories make an unreadable map | Areas view by default above the limits; search, focus and impact highlight |
| LLM cost and latency | Lookups skip the LLM; capped evidence; per-run budget; cheaper model for Ask |
| Prompt injection from repository text | Repository text is data inside delimiters; the LLM has no tools; injection fixture tests |
| GitHub rate limits | Optional token; history features report "not run" |

## 7. Inputs needed

- TypeSafe Jev docs and API key by **November 24** (Gate A).
- An Anthropic API key for M3 live runs (tests stay offline).
- Optional GitHub token for higher rate limits and history hotspots.
