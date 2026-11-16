import pytest

from agent_replay.experiments.scoring import ScoredDiagnosis, format_table, score_all, score_localizer


def d(
    predicted: int | None, true: int, ptype: str | None = "a", ttype: str = "a", tokens: int = 100
) -> ScoredDiagnosis:
    return ScoredDiagnosis(
        method="llm",
        predicted_step=predicted,
        true_step=true,
        predicted_type=ptype,
        true_type=ttype,
        tokens=tokens,
        seconds=2.0,
        run_id="r",
    )


def test_scoring_math_on_a_known_example() -> None:
    items = [
        d(3, 3),  # exact, type right
        d(4, 3, ptype="b"),  # one off, type wrong
        d(6, 3),  # far off, type right
        d(None, 3, ptype=None, tokens=0),  # no finding
    ]
    score = score_localizer("llm", items)
    assert score.n == 4
    assert (score.exact_step_accuracy.numerator, score.exact_step_accuracy.value) == (1, 0.25)
    assert (score.within_one_accuracy.numerator, score.within_one_accuracy.value) == (2, 0.5)
    assert (score.failure_type_accuracy.numerator, score.failure_type_accuracy.value) == (2, 0.5)
    assert score.no_finding.numerator == 1
    assert score.mean_tokens == 75 and score.mean_seconds == 2.0


def test_empty_input_gives_no_value_instead_of_dividing_by_zero() -> None:
    score = score_localizer("rule", [])
    assert score.exact_step_accuracy.value is None and score.mean_tokens is None


def test_score_all_groups_by_method_and_prints() -> None:
    items = [d(3, 3), d(3, 3).model_copy(update={"method": "rule", "predicted_step": 2})]
    scores = score_all(items)
    assert set(scores) == {"llm", "rule"}
    assert scores["rule"].exact_step_accuracy.value == 0 and scores["rule"].within_one_accuracy.value == 1
    table = format_table(scores)
    assert "100% (1/1)" in table and table.splitlines()[0].startswith("method")
    assert scores["llm"].mean_tokens == pytest.approx(100)
