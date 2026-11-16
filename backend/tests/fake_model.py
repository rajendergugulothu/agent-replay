"""A deterministic stand-in for the OpenAI client, for tests only.

It is not a model and nothing it produces is a research result. It plays a
planner that trusts its own plan: it assumes the answer to its k-th
prerequisite check is about the k-th course it meant to check, without reading
which course the tool actually answered for. That is the habit that lets an
injected wrong argument slip through. After a debugger note it becomes careful
and reads the course codes in the tool outputs.
"""

import json
import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from agent_replay.llm import response_from_dict


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return bool(set(a["days"]) & set(b["days"])) and a["start"] < b["end"] and b["start"] < a["end"]


def _history(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    outputs = {m["tool_call_id"]: json.loads(m["content"]) for m in messages if m["role"] == "tool"}
    calls = []
    for m in messages:
        for tc in m.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"]["arguments"])
            except ValueError:
                args = {}
            calls.append((tc["function"]["name"], args, outputs.get(tc["id"], {})))
    return calls


def plan_next(messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    """The next tool call, or None when the fake planner is finished."""
    user_text = " ".join(m["content"] for m in messages if m["role"] == "user")
    student_id = re.search(r"Student id: (\w+)", user_text).group(1)  # type: ignore[union-attr]
    earliest = "10:00" if "before 10am" in user_text else None
    careful = "Debugger note" in user_text
    history = _history(messages)

    requirements = [
        out for name, _, out in history if name == "get_degree_requirements" and "remaining_required" in out
    ]
    if not requirements:
        if sum(name == "get_degree_requirements" for name, _, _ in history) >= 2:
            return None
        return "get_degree_requirements", {"student_id": student_id}
    reqs = requirements[-1]
    credits = {c["course_code"]: c["credits"] for c in reqs["remaining_required"]}
    wanted = [code for code in re.findall(r"QCS \d{3}", user_text) if code not in reqs["completed"]]
    candidates = list(dict.fromkeys([*reqs["must_take_this_term"], *wanted, *credits]))

    checks = [(args, out) for name, args, out in history if name == "check_prerequisite" and "met" in out]
    if careful:
        verdicts = {out["course_code"]: out["met"] for _, out in checks}
    else:
        verdicts = {code: out["met"] for code, (_, out) in zip(candidates, checks, strict=False)}
    chosen: list[str] = []
    total = 0
    for code in candidates:
        if total >= reqs["min_credits"]:
            break
        if code not in verdicts:
            if sum(name == "check_prerequisite" for name, _, _ in history) >= 12:
                return None
            return "check_prerequisite", {"student_id": student_id, "course_code": code}
        if verdicts[code] and total + credits.get(code, 3) <= reqs["max_credits"]:
            chosen.append(code)
            total += credits.get(code, 3)

    searches = [out for name, _, out in history if name == "search_sections" and "sections" in out]
    if careful:
        found = {out["course_code"]: out["sections"] for out in searches}
    else:
        found = {code: out["sections"] for code, out in zip(chosen, searches, strict=False)}
    for code in chosen:
        if code not in found:
            if sum(name == "search_sections" for name, _, _ in history) >= 10:
                return None
            args: dict[str, Any] = {"course_code": code}
            if earliest:
                args["earliest_start"] = earliest
            return "search_sections", args

    picked: list[dict[str, Any]] = []
    for code in chosen:
        for section in found[code]:
            if not any(_overlap(section, other) for other in picked):
                picked.append(section)
                break
    section_ids = [s["section_id"] for s in picked]
    builds = [(args, out) for name, args, out in history if name == "build_schedule"]
    if builds and builds[-1][0].get("section_ids") == section_ids:
        return None
    if len(builds) >= 3 or not section_ids:
        return None
    return "build_schedule", {"student_id": student_id, "section_ids": section_ids}


def default_judge(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        "predicted_step": 3,
        "failure_type": "wrong_argument_values",
        "reason": "The course checked at this step is not the course that was scheduled.",
        "evidence": [{"step_number": 3, "field": "tool_args.course_code", "note": "differs from the plan"}],
    }


class FakeClient:
    def __init__(self, judge: Callable[[dict[str, Any]], Any] = default_judge) -> None:
        self.judge = judge
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        prompt_tokens = len(json.dumps(kwargs["messages"])) // 4
        if "response_format" in kwargs:
            reply = self.judge(kwargs)
            content = reply if isinstance(reply, str) else json.dumps(reply)
            return response_from_dict(
                {
                    "content": content,
                    "tool_calls": [],
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 60,
                }
            )
        step = plan_next(kwargs["messages"])
        if step is None:
            return response_from_dict(
                {
                    "content": "Here is your schedule.",
                    "tool_calls": [],
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 12,
                }
            )
        name, args = step
        # Ids must be unique within a conversation, as real ones are. The message
        # count grows with every step, including steps reused by a continuation.
        call = {"id": f"call_{len(kwargs['messages'])}", "name": name, "arguments": json.dumps(args)}
        return response_from_dict(
            {"content": None, "tool_calls": [call], "prompt_tokens": prompt_tokens, "completion_tokens": 25}
        )
