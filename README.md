# EvidenceLoop

**Understand a codebase before you change it.**

Paste a GitHub repository (or drop a ZIP). EvidenceLoop reads every file, maps how they connect, explains the
project in plain English, and points out what needs fixing, with the exact lines as proof. Nothing from the
repository is ever run.

- **Overview:** what the project is, how it's organised, where it starts, what to fix first, with a small
  preview of the map that opens the full one.
- **Map:** an Obsidian-style graph of every file and import. Hover to trace, click for details, and see everything a
  change would reach.
- **Issues:** import cycles, overly complex functions, silenced errors, untested hubs, undeclared or unpinned
  dependencies and more, ranked by how far each one spreads.
- **Files:** the code with each file's role, description, functions and complexity.
- **What is this file?** With a Google Gemini or Anthropic API key, each file also gets a short AI-written paragraph on what it does
  and what it contributes, plus the functions to read first (each checked against the real code).
- **Search and ask:** one box at the top right (Ctrl K) finds files, functions and text as you type, and answers
  lookups ("where is load_data", "what calls main", "which files use pandas") in place from the index, with no AI.
  Anything else goes to the **AI chat** (the button at the bottom right): open questions ("how is the model
  trained?") are answered with claims that each cite code; every citation is checked and each claim is marked
  verified, not enough evidence, or rejected. "Show in map" lights up the cited files. Past questions are kept
  per repository.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cd web && npm install && npm run build && cd ..
python -m cli.main serve             # then open http://127.0.0.1:8000
```

Checks: `ruff check .`, `pytest -q` (offline) `npm test` and `npm run build` in `web/`.

Use `127.0.0.1` rather than `localhost`: on Windows `localhost` tries IPv6 first and adds a
noticeable delay to every request. The server only answers on this machine. Without a token,
GitHub allows 60 API requests an hour (about 30 analyses); set a `GITHUB_TOKEN` environment
variable to raise that to 5,000.

## AI features (optional)

File explanations and open questions in Ask use a language model. Set one key before `serve`;
without one, everything else (lookups included) works the same.

| Provider | Key | Default model | Cost |
|---|---|---|---|
| Google Gemini | `GEMINI_API_KEY` (from Google AI Studio) | `gemini-3.5-flash-lite` | Free tier with daily limits |
| Anthropic Claude | `ANTHROPIC_API_KEY` | `claude-haiku-5-5` | Prepaid credits, about $0.001 per file |

If both keys are set, Claude is used unless `EL_PROVIDER=gemini`. `EL_MODEL_EXPLAIN` changes the
model. On Gemini's free tier, Google may use what is sent to improve its products; its paid tier
does not.

After an analysis the 10 most important files are explained in the background; any other file
is explained the first time it is opened. Each file's code (trimmed when large, with likely
secrets removed) and its facts are sent to the provider; "What was sent" under each explanation
says exactly what. Explanations are cached until the file changes.

An open question sends the question and up to 12 numbered pieces of code (`EL_MAX_EVIDENCE_ITEMS`)
to the answering model, then each claim and the code it cites to a judging model. One question
is capped at 60,000 tokens and $0.05 at list prices (`EL_MAX_TOKENS_PER_ASK`,
`EL_MAX_COST_PER_ASK`). A claim is verified when its citations are real and unchanged, and the
judge scores support at least 0.80, sufficiency at least 0.70 and contradiction under 0.50
(`EL_T_SUPPORT`, `EL_T_SUFFICIENCY`, `EL_T_CONTRADICTION`). These thresholds aren't calibrated yet.
`EL_MODEL_ASK` and `EL_MODEL_JUDGE` choose the models. Every call to a provider, with the exact
prompt, is logged in the `provider_sends` table.

## Learn more

- [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md): how it works, concepts, safety, adding a rule.
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): what's built and what comes next.
