"""Gate A: score each evaluator against the hand-labelled cases.

    python -m evaluation.gate_a.run                      # LLM judge, Jev 1.13 and Jev 1.14
    python -m evaluation.gate_a.run --evaluators jev-1.14.0 --limit 10

Only cases with a human_label are used. Each evaluator judges every case's claim against its
evidence. The report gives, per evaluator: accuracy at the current thresholds (EL_T_*), the
best threshold per field and jointly, Brier scores, how often a not_supported or
related_not_supporting claim would wrongly show as verified, p50/p95 latency and cost per
judgment. Raw results go to evaluation/gate_a/results/ as JSON. An evaluator whose key isn't set
is skipped and the report says so.

The LLM judge needs GEMINI_API_KEY or ANTHROPIC_API_KEY; Jev needs TYPESAFE_API_KEY.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import yaml

from evaluation.gate_a import metrics
from intelligence.budget import Budget
from intelligence.evaluators import Evaluator, EvaluatorUnavailable, Thresholds
from intelligence.evaluators.jev import JEV_VERSIONS, JevEvaluator, jev_status
from intelligence.evaluators.llm_judge import LlmJudgeEvaluator
from intelligence.providers import ai_status, provider_for
from intelligence.sendlog import Metered
from retrieval.evidence import EvidenceItem

HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "candidates.yaml"
RESULTS_DIR = HERE / "results"
DEFAULT_EVALUATORS = ("llm", *JEV_VERSIONS)

# name -> (factory taking a Metered, or None and why it can't run)
Factory = Callable[[Metered], Evaluator]


def load_cases(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [c for c in data["cases"] if c.get("human_label") in metrics.LABELS]


def evidence_of(case: dict) -> list[EvidenceItem]:
    e = case["evidence"]
    return [EvidenceItem("E1", e["path"], e["start"], e["end"], "code", e["path"], e["text"],
                         hashlib.sha256(e["text"].encode("utf-8")).hexdigest())]


def factories() -> dict[str, tuple[Factory | None, str]]:
    out: dict[str, tuple[Factory | None, str]] = {}
    judge = provider_for("judge")
    out["llm"] = ((lambda m: LlmJudgeEvaluator(judge, m)), "") if judge else \
        (None, ai_status()[1] or "No LLM provider is configured.")
    ready, why = jev_status()
    for version in JEV_VERSIONS:
        out[version] = ((lambda m, v=version: JevEvaluator(version=v, metered=m)), "") if ready \
            else (None, why)
    return out


def judge_all(name: str, factory: Factory, cases: list[dict], delay: float = 0.0,
              progress: Callable[[str], None] = print) -> list[dict]:
    rows = []
    for i, case in enumerate(cases, 1):
        metered = Metered(Budget(max_tokens=10**9, max_cost=10.0**9))
        evaluator = factory(metered)
        started = time.perf_counter()
        row = {"case_id": case["id"], "label": case["human_label"], "evaluator": name,
               "support": None, "contradiction": None, "sufficiency": None, "model_version": None,
               "reason": None, "error": None}
        try:
            judgment = evaluator.judge(case["claim"], evidence_of(case))
            row.update(support=judgment.support, contradiction=judgment.contradiction,
                       sufficiency=judgment.sufficiency, model_version=judgment.model_version,
                       reason=judgment.reason)
        except EvaluatorUnavailable as exc:
            row["error"] = str(exc)
        row["latency_s"] = round(time.perf_counter() - started, 3)
        usage = metered.budget.summary()
        row.update(input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                   cost_usd=usage["cost_usd"])
        rows.append(row)
        progress(f"  {name} {i}/{len(cases)} {case['id']}: "
                 + (row["error"] or f"s={row['support']:.2f} c={row['contradiction']:.2f} "
                                    f"f={row['sufficiency']:.2f}"))
        if delay:
            time.sleep(delay)
    return rows


def _fmt(value, digits: int = 2, pct: bool = False) -> str:
    if value is None:
        return "–"
    return f"{value * 100:.0f}%" if pct else f"{value:.{digits}f}"


def _money(value: float | None) -> str:
    return "–" if value is None else f"${value:.6f}"


def report_table(summaries: dict[str, dict], skipped: dict[str, str]) -> str:
    lines = [
        "| Evaluator | Judged | Accuracy (current) | False verified | Missed | Brier s / c / f "
        "| Calibrated thresholds s / f / c | Accuracy (calibrated) | False verified (cal.) "
        "| p50 / p95 latency | Cost / judgment |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, s in summaries.items():
        b, t = s["brier"], s["calibrated_thresholds"] or {}
        lines.append(
            f"| {name} | {s['judgments'] - s['failed']}/{s['judgments']} "
            f"| {_fmt(s['accuracy'], pct=True)} | {_fmt(s['false_verified_rate'], pct=True)} "
            f"| {_fmt(s['missed_rate'], pct=True)} "
            f"| {_fmt(b['support'])} / {_fmt(b['contradiction'])} / {_fmt(b['sufficiency'])} "
            f"| {_fmt(t.get('support'))} / {_fmt(t.get('sufficiency'))} / "
            f"{_fmt(t.get('contradiction'))} "
            f"| {_fmt(s['calibrated_accuracy'], pct=True)} "
            f"| {_fmt(s['calibrated_false_verified_rate'], pct=True)} "
            f"| {_fmt(s['latency_p50_s'])} s / {_fmt(s['latency_p95_s'])} s "
            f"| {_money(s['cost_per_judgment_usd'])} |"
        )
    for name, why in skipped.items():
        lines.append(f"| {name} | skipped: {why} | | | | | | | | | |")
    return "\n".join(lines)


def recommendation(summaries: dict[str, dict]) -> str:
    llm = summaries.get("llm")
    jevs = {n: s for n, s in summaries.items() if n.startswith("jev")}
    if not llm or not jevs:
        return "Not decided: both the LLM judge and at least one Jev version must be run."
    winners = [n for n, s in jevs.items() if metrics.beats(s, llm)]
    if not winners:
        return ("Jev doesn't beat the LLM judge on this set: keep the LLM judge as the default and "
                "Jev as an optional evaluator (EL_EVALUATOR=jev).")
    best = max(winners, key=lambda n: (jevs[n]["calibrated_accuracy"],
                                       -(jevs[n]["calibrated_false_verified_rate"] or 0)))
    return (f"{best} beats the LLM judge: make it the default "
            f"(EL_EVALUATOR=jev, EL_JEV_VERSION={best}).")


def run(cases_path: Path, names: list[str], limit: int | None = None, delay: float = 0.0,
        out_dir: Path = RESULTS_DIR, available: dict | None = None,
        progress: Callable[[str], None] = print) -> dict:
    cases = load_cases(cases_path)[:limit] if limit else load_cases(cases_path)
    if not cases:
        raise SystemExit(f"No case in {cases_path} has a human_label yet. Label some, then run "
                         "again.")
    env = Thresholds.from_env()
    current = metrics.Thresholds(env.support, env.sufficiency, env.contradiction)
    available = available if available is not None else factories()
    rows: list[dict] = []
    summaries: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    for name in names:
        factory, why = available.get(name, (None, f"unknown evaluator {name!r}"))
        if factory is None:
            skipped[name] = why
            progress(f"Skipping {name}: {why}")
            continue
        progress(f"Running {name} on {len(cases)} cases")
        mine = judge_all(name, factory, cases, delay, progress)
        rows += mine
        summaries[name] = metrics.summarize(mine, current)

    result = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "cases_file": str(cases_path),
        "cases": len(cases),
        "labels": {label: sum(c["human_label"] == label for c in cases)
                   for label in metrics.LABELS},
        "current_thresholds": current.to_dict(),
        "summaries": summaries,
        "skipped": skipped,
        "recommendation": recommendation(summaries),
        "table": report_table(summaries, skipped),
        "rows": rows,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"gate-a-{stamp}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    result["saved_to"] = str(path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--evaluators", default=",".join(DEFAULT_EVALUATORS),
                        help="comma-separated: llm, jev-1.13.0, jev-1.14.0")
    parser.add_argument("--limit", type=int, default=None, help="only the first N labelled cases")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="seconds between judgments (for free-tier rate limits)")
    args = parser.parse_args(argv)
    result = run(args.cases, [n.strip() for n in args.evaluators.split(",") if n.strip()],
                 args.limit, args.delay)
    print()
    print(f"{result['cases']} labelled cases: {result['labels']}")
    print(f"Current thresholds: {result['current_thresholds']}")
    print()
    print(result["table"])
    print()
    print(result["recommendation"])
    print(f"\nRaw results: {result['saved_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
