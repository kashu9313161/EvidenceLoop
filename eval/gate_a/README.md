# Gate A ground-truth cases

Gate A measures how well an evaluator decides whether cited evidence supports a claim
(`judge(claim, evidence)`, see `docs/decisions/gate-a.md`). This folder holds the 40
claim/evidence pairs that the evaluators are scored against: 20 supports, 10 not_supported,
5 related_not_supporting and 5 ambiguous, taken from the three analysed repositories
(itsdangerous, markupsafe, ML-Projects). That is the mix the plan asks for.

It builds on `evaluation/gate_a/` rather than replacing it. The runner (`run.py`) and the
metrics (`metrics.py`) live there, and the label names here are `metrics.LABELS`. This folder
only produces and checks the cases. `run.py` reads `cases.json` when given `--cases` and
refuses to score it unless `verify_cases.py` passes. The older 60 drafted candidates in
`evaluation/gate_a/candidates.yaml` remain `run.py`'s default.

| File | What it is |
|---|---|
| `curated.py` | Hand-written claims, each pinned to a line range checked against the source |
| `build_cases.py` | Builds `cases.json` and `LABEL_SHEET.md` from the curated claims, the stored reports and the snapshots |
| `cases.json` | The 40 cases. You fill in `label` (and optionally `labeller_note`) here |
| `LABEL_SHEET.md` | The same cases to read: each claim with its numbered snippet inline |
| `verify_cases.py` | Re-reads every snippet off disk and checks the set and your labels |

## Steps

The snapshots live in `data/` (gitignored), so all three steps need the analysed repositories
on this machine.

**1. Build** (already done; re-run only if `curated.py` or the snapshots change):

```bash
python -m eval.gate_a.build_cases
```

Every snippet is read from the snapshot on disk. Nothing is quoted from memory. A rebuild keeps
the labels of cases that come out identical. The case order is shuffled with a fixed seed, so
the kinds don't appear in blocks.

**2. Label.** Read `LABEL_SHEET.md` and set `"label"` for each id in `cases.json` to one of the
four labels below. Judge only from the lines shown: if the proof is one line outside the range,
the evidence doesn't support the claim. The sheet hides how each case was built, so that can't
steer you. Check your progress and your spelling with:

```bash
python -m eval.gate_a.verify_cases
```

It exits 1 on any problem: a snippet that no longer matches the file, a misspelt label, a
missing field or wrong bucket counts. It also lists every case where your label differs from
`constructed_as`, with the note explaining how the case was built. Look at each one. A
difference is fine when you meant it.

**3. Run the evaluators:**

```bash
python -m evaluation.gate_a.run --cases eval/gate_a/cases.json
```

Only labelled cases are scored. If verification fails, the run stops before any evaluator is
called. Record results in `docs/decisions/gate-a.md`.

## The four labels

| Label | Meaning |
|---|---|
| `supports` | The lines shown are enough, on their own, to show the claim is true |
| `not_supported` | The lines don't bear on the claim, or show it is false |
| `related_not_supporting` | The evidence looks relevant (same file, same names, same topic) but doesn't establish the claim |
| `ambiguous` | Settled only under an assumption the evidence doesn't state, or the claim over-generalises ("all", "every", "is safe", "is ready") beyond what the lines show |

**Only `label` scores an evaluator.** `constructed_as` is what the generator aimed for, and
`construction_note` says how. They exist so `verify_cases.py` can point out differences, not
as ground truth. If the code says something other than the generator assumed, your label wins.

### Why related_not_supporting matters most

In use, Ask retrieves evidence by relevance, so the cited code nearly always shares names
and topic with the claim. The dangerous error is not verifying a claim against unrelated code,
which any evaluator catches. It is verifying a claim against code that mentions the right
things but doesn't prove them. For example, `Signer.verify_signature` calls the constant-time
comparison but doesn't contain it. A `/health` route sits right next to the model-loading code.
These cases test whether an evaluator reads the evidence or matches keywords. A wrong
"verified" on one of them is the costly mistake. `run.py` reports it as the false verified
rate, and per label.

### Why ambiguous cases are scored separately

For an ambiguous claim, the right outcome is "not verified", but a careful evaluator can
reasonably lean either way. These cases count toward accuracy as should-not-verify. They are
left out of the headline false verified rate, which covers only `not_supported` and
`related_not_supporting`, and are reported on their own line (`false_verified_by_label`). So a
lean on borderline claims doesn't hide, or inflate, the error that matters.

## How the cases are built

- **supports (20):**
  - 14 hand-written claims about what the code does (`curated.SUPPORTED`). Examples: the
    constant-time comparison, the empty `NoneAlgorithm` signature, the `_speedups` import with
    a `_native` fallback, `Markup.__mod__` escaping arguments before substituting, an API that
    loads its model at import time from a hard-coded path, a `/health` that checks nothing, a
    request schema with no bounds.
  - 6 topped up from the stored reports. These use only rules whose cited lines contain the
    proof (`swallowed_exception`, line counts from `complex_function`) and confirmed import
    edges whose lines are all import statements, each spanning at least 2 lines.
  - Rules like `untested_hub`, `unpinned_dependencies`, `utf16_manifest` and `god_class` are
    excluded. They cite one line that locates a finding without evidencing it, so a supports
    case built from them would be mislabelled by construction.
- **not_supported (10):** a true claim (`curated.MOVED`) with its evidence moved to a real
  function body in an unrelated file. The source repository rotates, and no snippet is ever
  reused. The note records where the real proof is.
- **related_not_supporting (5)** and **ambiguous (5)** are hand-written. Each carries a note
  explaining why.

No two cases share or overlap a snippet, so a labeller can't recognise evidence instead of
reading it.

## Known caveat

`metrics.targets` sets the contradiction target to 1 for `not_supported`, which fits "the
evidence shows the claim is false". Every constructed `not_supported` case here uses unrelated
evidence, which doesn't contradict anything. An evaluator that answers contradiction ≈ 0 on
these is right, but its contradiction Brier score is penalised. Accuracy, the false verified
rate and the calibrated thresholds don't use that target, so they are unaffected. Read the
contradiction Brier for this set with that in mind.
