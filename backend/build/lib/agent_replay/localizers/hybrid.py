"""Hybrid localizer: code checks first, the LLM judge only when they find nothing."""

from agent_replay.localizers.base import Diagnosis, LocalizerView, Method
from agent_replay.localizers.llm_judge import LLMJudgeLocalizer
from agent_replay.localizers.rules import RuleBasedLocalizer


class HybridLocalizer:
    method: Method = "hybrid"

    def __init__(self, judge: LLMJudgeLocalizer) -> None:
        self.rules = RuleBasedLocalizer()
        self.judge = judge

    def localize(self, view: LocalizerView) -> Diagnosis:
        by_rules = self.rules.localize(view)
        if by_rules.predicted_step is not None:
            return by_rules.model_copy(update={"method": "hybrid"})
        by_judge = self.judge.localize(view, checks_passed=by_rules.checks_passed)
        return by_judge.model_copy(
            update={"method": "hybrid", "seconds": by_judge.seconds + by_rules.seconds}
        )
