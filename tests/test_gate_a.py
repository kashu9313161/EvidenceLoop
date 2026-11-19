"""Gate A metrics and runner (offline, with stand-in evaluators)."""

import json

import pytest
import yaml

from evaluation.gate_a import metrics, run
from evaluation.gate_a.metrics import Thresholds
from intelligence.evaluators import EvaluatorUnavailable, Judgment

CURRENT = Thresholds()  # 0.80 / 0.70 / 0.50


def row(label, s=None, c=None, f=None, latency=0.5, cost=0.001, error=None):
    return {"label": label, "support": s, "contradiction": c, "sufficiency": f,
            "latency_s": latency, "cost_usd": cost, "error": error}


ROWS = [
    row("supports", 0.95, 0.05, 0.90),         # verified, correct
    row("supports", 0.70, 0.05, 0.90),         # missed at 0.80
    row("not_supported", 0.10, 0.90, 0.85),    # not verified, correct
    row("related_not_supporting", 0.85, 0.10, 0.75),  # wrongly verified at current
    row("ambiguous", 0.60, 0.30, 0.40),        # not verified, correct
    row("supports", error="TypeSafe returned an error (503).", latency=2.0, cost=None),
]


def test_accuracy_false_verified_and_missed_at_the_current_thresholds():
    assert metrics.accuracy(ROWS, CURRENT) == pytest.approx(3 / 5)  # the failed row is excluded
    assert metrics.false_verified_rate(ROWS, CURRENT) == pytest.approx(1 / 2)
    assert metrics.false_verified_rate(ROWS, CURRENT, ("not_supported",)) == 0
    assert metrics.missed_rate(ROWS, CURRENT) == pytest.approx(1 / 2)


def test_brier_scores_against_label_targets():
    scores = metrics.brier([row("supports", 1.0, 0.0, 1.0), row("not_supported", 0.5, 1.0, 1.0)])
    assert scores == {"support": pytest.approx(0.125), "contradiction": 0.0, "sufficiency": 0.0}


def test_calibration_finds_thresholds_that_separate_the_cases():
    best = metrics.best_joint(ROWS, CURRENT)
    assert metrics.accuracy(ROWS, best) == 1.0
    assert 0.70 < best.support <= 0.85 or best.sufficiency > 0.75  # either cut removes the error
    per_field = metrics.best_per_field(ROWS, CURRENT)
    assert set(per_field) == {"support", "sufficiency", "contradiction"}
    assert per_field["sufficiency"]["accuracy"] >= metrics.accuracy(ROWS, CURRENT)


def test_percentiles_and_summary():
    assert metrics.percentile([0.1, 0.2, 0.3, 0.4, 5.0], 50) == 0.3
    assert metrics.percentile([0.1, 0.2, 0.3, 0.4, 5.0], 95) == 5.0
    assert metrics.percentile([], 50) is None
    summary = metrics.summarize(ROWS, CURRENT)
    assert summary["judgments"] == 6 and summary["failed"] == 1
    assert summary["calibrated_accuracy"] == 1.0
    assert summary["cost_per_judgment_usd"] == pytest.approx(0.001)
    assert summary["latency_p95_s"] == 2.0  # failures count toward latency


@pytest.mark.parametrize(("jev", "llm", "expected"), [
    ({"calibrated_accuracy": 0.9, "calibrated_false_verified_rate": 0.1},
     {"calibrated_accuracy": 0.8, "calibrated_false_verified_rate": 0.1}, True),
    ({"calibrated_accuracy": 0.9, "calibrated_false_verified_rate": 0.3},
     {"calibrated_accuracy": 0.8, "calibrated_false_verified_rate": 0.1}, False),  # more wrong
    ({"calibrated_accuracy": 0.8, "calibrated_false_verified_rate": 0.0},
     {"calibrated_accuracy": 0.8, "calibrated_false_verified_rate": 0.1}, True),
    ({"calibrated_accuracy": 0.8, "calibrated_false_verified_rate": 0.1},
     {"calibrated_accuracy": 0.8, "calibrated_false_verified_rate": 0.1}, False),  # a tie
])
def test_the_gate_a_rule(jev, llm, expected):
    assert metrics.beats(jev, llm) is expected


# --------------------------------------------------------------------------- runner


class ByLabel:
    """A stand-in evaluator that scores each claim from a table keyed by claim text."""

    name = "fixed"

    def __init__(self, scores, fail_on=()):
        self.scores, self.fail_on = scores, fail_on

    def judge(self, claim, evidence):
        assert evidence[0].text and evidence[0].id == "E1"
        if claim in self.fail_on:
            raise EvaluatorUnavailable("down")
        s, c, f = self.scores[claim]
        return Judgment(s, c, f, "fixed", "fixed-1", "")


CASES = [
    ("a", "supports", (0.95, 0.05, 0.9)),
    ("b", "not_supported", (0.1, 0.9, 0.9)),
    ("c", "related_not_supporting", (0.85, 0.1, 0.8)),
    ("d", "ambiguous", (0.5, 0.3, 0.4)),
]


@pytest.fixture
def cases_file(tmp_path):
    data = {"version": 1, "cases": [
        {"id": f"t-{claim}", "claim": claim, "human_label": label, "suggested_label": label,
         "evidence": {"path": "x.py", "start": 1, "end": 1, "text": "x = 1"}}
        for claim, label, _ in CASES
    ] + [{"id": "t-unlabelled", "claim": "e", "human_label": None,
          "evidence": {"path": "x.py", "start": 1, "end": 1, "text": "x = 1"}}]}
    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_the_runner_judges_labelled_cases_and_saves_raw_results(cases_file, tmp_path,
                                                               monkeypatch):
    for name in ("EL_T_SUPPORT", "EL_T_SUFFICIENCY", "EL_T_CONTRADICTION"):
        monkeypatch.delenv(name, raising=False)
    good = {claim: scores for claim, _, scores in CASES}
    # The LLM stand-in scores the related-but-not claim exactly like the supported one, so no
    # thresholds can tell them apart; the Jev stand-in separates them.
    confused = {**good, "c": good["a"]}
    perfect = {**good, "c": (0.3, 0.1, 0.8)}
    available = {
        "llm": (lambda m: ByLabel(confused), ""),
        "jev-1.13.0": (lambda m: ByLabel(perfect, fail_on=("d",)), ""),
        "jev-1.14.0": (None, "Set TYPESAFE_API_KEY to use Jev."),
    }
    result = run.run(cases_file, ["llm", "jev-1.13.0", "jev-1.14.0"], out_dir=tmp_path / "out",
                     available=available, progress=lambda _: None)

    assert result["cases"] == 4  # the unlabelled case is left out
    assert result["labels"]["supports"] == 1
    assert result["skipped"] == {"jev-1.14.0": "Set TYPESAFE_API_KEY to use Jev."}
    llm, jev = result["summaries"]["llm"], result["summaries"]["jev-1.13.0"]
    assert llm["false_verified_rate"] == pytest.approx(0.5)  # "c" wrongly verified
    assert jev["false_verified_rate"] == 0 and jev["failed"] == 1
    assert "jev-1.13.0 beats the LLM judge" in result["recommendation"]
    assert "| jev-1.14.0 | skipped: Set TYPESAFE_API_KEY" in result["table"]

    saved = json.loads((tmp_path / "out" / result["saved_to"].split("\\")[-1].split("/")[-1])
                       .read_text(encoding="utf-8"))
    assert len(saved["rows"]) == 8 and saved["current_thresholds"]["support"] == 0.8
    assert {r["evaluator"] for r in saved["rows"]} == {"llm", "jev-1.13.0"}


def test_the_recommendation_follows_the_plan_when_jev_does_not_win():
    same = {"calibrated_accuracy": 0.8, "calibrated_false_verified_rate": 0.1}
    text = run.recommendation({"llm": same, "jev-1.14.0": same})
    assert "keep the LLM judge as the default" in text
    assert "Not decided" in run.recommendation({"llm": same})


def test_the_runner_needs_labels(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text(yaml.safe_dump({"cases": [{"id": "x", "claim": "c", "human_label": None,
                                               "evidence": {"path": "p", "start": 1, "end": 1,
                                                            "text": "t"}}]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="has a human_label yet"):
        run.run(path, ["llm"], available={}, progress=lambda _: None)


def test_the_real_candidates_file_loads():
    cases = yaml.safe_load(run.DEFAULT_CASES.read_text(encoding="utf-8"))["cases"]
    assert len(cases) == 60
    assert all(c["evidence"]["text"] for c in cases)
