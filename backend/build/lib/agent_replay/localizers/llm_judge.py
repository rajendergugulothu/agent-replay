"""LLM judge localizer. The prompt lives in judge_prompt.md so it is easy to
read and change."""

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from agent_replay.localizers.base import Diagnosis, Evidence, LocalizerView, Method
from agent_replay.localizers.rules import compact_trace
from agent_replay.models import FailureType

PROMPT_PATH = Path(__file__).with_name("judge_prompt.md")

RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "diagnosis",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["predicted_step", "failure_type", "reason", "evidence"],
            "properties": {
                "predicted_step": {"type": ["integer", "null"]},
                "failure_type": {
                    "type": ["string", "null"],
                    "enum": [*(t.value for t in FailureType), None],
                },
                "reason": {"type": "string"},
                "evidence": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["step_number", "field", "note"],
                        "properties": {
                            "step_number": {"type": "integer"},
                            "field": {"type": "string"},
                            "note": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}


class _JudgeReply(BaseModel):
    predicted_step: int | None
    failure_type: FailureType | None
    reason: str
    evidence: list[Evidence] = []


def build_prompt(view: LocalizerView, checks_passed: list[str] | None = None) -> str:
    checks_section = ""
    if checks_passed:
        checks_section = (
            "Automatic checks already ran on every step and found nothing: "
            + ", ".join(checks_passed)
            + ". The error is therefore one that code could not catch.\n\n"
        )
    reasons = view.validation.reasons if view.validation else []
    return PROMPT_PATH.read_text().format(
        checks_section=checks_section,
        user_request=view.user_request,
        trace=compact_trace(view),
        outcome=f"{view.status} ({view.stop_reason})",
        validator_reasons="\n".join(f"- {r.code}: {r.message}" for r in reasons) or "- none",
    )


class LLMJudgeLocalizer:
    method: Method = "llm"

    def __init__(
        self, client: Any, model: str, temperature: float | None = 0.0, max_completion_tokens: int = 4_000
    ) -> None:
        self.client = client
        self.model = model
        self.temperature = temperature
        self.max_completion_tokens = max_completion_tokens

    def localize(self, view: LocalizerView, checks_passed: list[str] | None = None) -> Diagnosis:
        started = time.perf_counter()
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": build_prompt(view, checks_passed)}],
            "response_format": RESPONSE_FORMAT,
            "max_completion_tokens": self.max_completion_tokens,
        }
        if self.temperature is not None:
            request["temperature"] = self.temperature
        response = self.client.chat.completions.create(**request)
        raw = response.choices[0].message.content or ""
        common: dict[str, Any] = {
            "method": "llm",
            "tokens": response.usage.prompt_tokens + response.usage.completion_tokens,
            "seconds": time.perf_counter() - started,
            "used_llm": True,
            "judge_model": self.model,
            "judge_temperature": self.temperature,
            "raw_response": raw,
            "checks_passed": checks_passed or [],
        }
        try:
            reply = _JudgeReply.model_validate(json.loads(raw))
        except (ValueError, ValidationError):
            # A judge that answers badly still spent tokens. Count them and
            # report no finding instead of crashing the experiment.
            return Diagnosis(
                predicted_step=None,
                failure_type=None,
                reason="The judge did not return a usable diagnosis.",
                **common,
            )
        step_numbers = {s.step_number for s in view.steps}
        if reply.predicted_step is not None and reply.predicted_step not in step_numbers:
            return Diagnosis(
                predicted_step=None,
                failure_type=None,
                reason=f"The judge named step {reply.predicted_step}, which is not in the run.",
                **common,
            )
        return Diagnosis(
            predicted_step=reply.predicted_step,
            failure_type=reply.failure_type if reply.predicted_step is not None else None,
            reason=reply.reason,
            evidence=[e for e in reply.evidence if e.step_number in step_numbers],
            **common,
        )
