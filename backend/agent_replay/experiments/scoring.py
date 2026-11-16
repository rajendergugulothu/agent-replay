"""Localization scoring against labels."""

from pydantic import BaseModel


class ScoredDiagnosis(BaseModel):
    method: str
    predicted_step: int | None
    true_step: int
    predicted_type: str | None
    true_type: str
    tokens: int
    seconds: float
    run_id: str = ""


class Ratio(BaseModel):
    value: float | None
    numerator: int
    denominator: int


def ratio(numerator: int, denominator: int) -> Ratio:
    return Ratio(
        value=numerator / denominator if denominator else None,
        numerator=numerator,
        denominator=denominator,
    )


class LocalizerScore(BaseModel):
    method: str
    n: int
    exact_step_accuracy: Ratio
    within_one_accuracy: Ratio
    failure_type_accuracy: Ratio
    no_finding: Ratio
    mean_tokens: float | None
    mean_seconds: float | None
    run_ids: list[str]


def score_localizer(method: str, items: list[ScoredDiagnosis]) -> LocalizerScore:
    """A diagnosis with no finding counts as a miss on every accuracy."""
    n = len(items)
    exact = sum(d.predicted_step == d.true_step for d in items)
    within = sum(d.predicted_step is not None and abs(d.predicted_step - d.true_step) <= 1 for d in items)
    type_ok = sum(d.predicted_type == d.true_type for d in items)
    none = sum(d.predicted_step is None for d in items)
    return LocalizerScore(
        method=method,
        n=n,
        exact_step_accuracy=ratio(exact, n),
        within_one_accuracy=ratio(within, n),
        failure_type_accuracy=ratio(type_ok, n),
        no_finding=ratio(none, n),
        mean_tokens=sum(d.tokens for d in items) / n if n else None,
        mean_seconds=sum(d.seconds for d in items) / n if n else None,
        run_ids=[d.run_id for d in items if d.run_id],
    )


def score_all(items: list[ScoredDiagnosis]) -> dict[str, LocalizerScore]:
    methods = sorted({d.method for d in items})
    return {m: score_localizer(m, [d for d in items if d.method == m]) for m in methods}


def format_table(scores: dict[str, LocalizerScore]) -> str:
    def pct(r: Ratio) -> str:
        return "n/a" if r.value is None else f"{r.value:.0%} ({r.numerator}/{r.denominator})"

    rows = [("method", "n", "exact step", "within one", "failure type", "mean tokens", "mean seconds")]
    for s in scores.values():
        rows.append(
            (
                s.method,
                str(s.n),
                pct(s.exact_step_accuracy),
                pct(s.within_one_accuracy),
                pct(s.failure_type_accuracy),
                "n/a" if s.mean_tokens is None else f"{s.mean_tokens:.0f}",
                "n/a" if s.mean_seconds is None else f"{s.mean_seconds:.2f}",
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)) for row in rows)
