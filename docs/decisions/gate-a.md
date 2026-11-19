# Gate A: which evaluator judges Ask's claims

**Status: not run yet (Nov 19, 2026).** Everything needed to run it is built and tested offline.
Two inputs are missing:

1. **Human labels.** `evaluation/gate_a/candidates.yaml` has 60 drafted cases, but none has a
   `human_label` yet.
2. **A TypeSafe API key** (`TYPESAFE_API_KEY`) for Jev. The LLM judge also needs a Gemini or
   Anthropic key in the shell that runs the evaluation.

Until Gate A is run, nothing below is a result, and nothing in the product changes:

- The default evaluator stays the **LLM judge**.
- The thresholds stay at **0.80 support, 0.70 sufficiency, 0.50 contradiction**.
- The Ask UI keeps marking the scores **"uncalibrated"**.

## Question

When Ask checks a claim against the code it cites, which evaluator should score it, and at what
thresholds should a claim show as **verified**?

## Candidates

All three run behind the same `Evaluator` protocol:
`judge(claim, evidence) -> Judgment(support, contradiction, sufficiency, ...)`.

| Evaluator | How it scores | Pinned version |
|---|---|---|
| LLM judge | The configured provider (Gemini or Claude) returns three scores from 0 to 1 as structured JSON | `EL_MODEL_JUDGE`, or the provider default |
| Jev 1.13 | One TypeSafe System One call with three noul questions (below) | `jev-1.13.0` |
| Jev 1.14 | Same questions | `jev-1.14.0` |

The Jev questions map to the Judgment fields:

- **support:** "Does this code directly show the claim, not just mention related names?"
- **contradiction:** "Does this code show the claim is false or already handled differently?"
- **sufficiency:** "Is this evidence enough to decide?"

Each answer is a noul probability. Jev runs without long-context mode, the version is always
pinned, and the state is small: the claim plus the cited code, at most 4,000 characters per item
and 12,000 in total. Retries honour `Retry-After`; an outage, a bad key or an unusable answer
makes the evaluator unavailable, and the claim shows "not enough evidence".

## Cases

There are 60 drafted candidates from three analysed repositories: itsdangerous 24, markupsafe 14
and ML-Projects 22. Suggested labels: 25 supports, 15 not_supported, 10 related_not_supporting
and 10 ambiguous. Only cases with a `human_label` count. The plan asks for at least 40 labelled
cases.

## Metrics

All metrics come from `evaluation/gate_a/run.py` and `evaluation/gate_a/metrics.py`.

- **Should be verified:** only claims labelled `supports`. Every other label should not show as
  verified.
- **Accuracy:** the share of judged claims where "verified" matches "should be verified".
  Reported at the current thresholds and at each evaluator's calibrated thresholds.
- **False verified rate:** the share of `not_supported` and `related_not_supporting` claims that
  would show as verified. This is the costly mistake, so it is reported on its own, per label,
  with `ambiguous` reported as well.
- **Missed rate:** the share of `supports` claims that would not show as verified.
- **Brier score per field,** against targets taken from the label:
  - support is 1 for `supports`;
  - contradiction is 1 for `not_supported`;
  - sufficiency is 1 for `supports` and `not_supported`, the labels where the evidence decides;
  - every other case has a target of 0.
- **Calibrated thresholds:** the best combination on a 0.05 grid. Ties go to the fewest false
  verifications, then to the combination closest to the current thresholds. The best threshold
  for each field on its own is also reported.
- **Latency:** p50 and p95 per judgment, with retries included.
- **Cost per judgment:**
  - LLM judge: the provider's list price;
  - Jev: $0.042 per million input tokens (output tokens are free).

## Decision rule

The rule follows the plan: "If Jev doesn't beat the LLM judge in Gate A, keep it as an optional
evaluator and report the result." Made precise, a Jev version **beats** the LLM judge when, each
at its own calibrated thresholds:

- it has **higher accuracy and no higher false verified rate**, or
- it has **equal accuracy and a lower false verified rate**.

A tie is not a win.

- **If a Jev version beats the LLM judge,** make it the default (`EL_EVALUATOR=jev`,
  `EL_JEV_VERSION=` that version), at its calibrated thresholds.
- **If not,** keep the LLM judge as the default, at its calibrated thresholds, with Jev available
  as `EL_EVALUATOR=jev`.

**Thresholds move to the calibrated values,** and the "uncalibrated" label comes off the UI,
only when:

- at least 40 labelled cases were judged;
- at least 10 of them are `not_supported` or `related_not_supporting`; and
- the calibrated thresholds are not worse on false verified rate than the current ones.

**A caveat:** with 40–60 cases, thresholds tuned on the same cases flatter the result. Report the
accuracy at both the current and the calibrated thresholds, and treat a difference of one or two
cases as noise.

## Results

_Not run yet._ After running, paste the table the runner prints here. Also record:

- the label counts;
- the raw results file in `evaluation/gate_a/results/`;
- the recommendation line the runner prints;
- your decision, if it differs from that recommendation, and why.

| Evaluator | Judged | Accuracy (current) | False verified | Missed | Brier s / c / f | Calibrated thresholds s / f / c | Accuracy (calibrated) | False verified (cal.) | p50 / p95 latency | Cost / judgment |
|---|---|---|---|---|---|---|---|---|---|---|
| LLM judge | – | – | – | – | – | – | – | – | – | – |
| jev-1.13.0 | – | – | – | – | – | – | – | – | – | – |
| jev-1.14.0 | – | – | – | – | – | – | – | – | – | – |

**Chosen default evaluator:** _pending._
**Calibrated thresholds:** _pending._ The current defaults stay until then.

## How to run

```bash
pip install -e ".[dev]"
```

Fill in `human_label` in `evaluation/gate_a/candidates.yaml`. Then, in PowerShell:

```bash
$env:GEMINI_API_KEY="..."; $env:TYPESAFE_API_KEY="..."
```

```bash
python -m evaluation.gate_a.run
```

Options:

- `--evaluators llm,jev-1.13.0,jev-1.14.0` picks which evaluators run.
- `--limit 10` runs a quick trial on the first 10 cases.
- `--delay 4` waits 4 seconds between judgments, which keeps Gemini's free tier under its
  per-minute limit.

An evaluator whose key isn't set is skipped, and the report says so.

**After the run,** apply the decision rule:

1. Set the new defaults for `EL_T_SUPPORT`, `EL_T_SUFFICIENCY` and `EL_T_CONTRADICTION` in
   `intelligence/evaluators/__init__.py` (`Thresholds`).
2. Set `calibrated=True` there, which removes the "uncalibrated" tag in the UI.
3. If Jev won, change the default evaluator in `intelligence/evaluators/choice.py`.
4. Fill in the Results section above.
