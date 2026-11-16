You are reviewing a failed run of a tool-using AI agent. Find the step where the run first went wrong. That is often earlier than the step where the failure became visible.

The agent is a semester planner. Its tools:
- get_degree_requirements(student_id): completed courses, remaining required courses, courses that must be taken this term, credit limits.
- check_prerequisite(student_id, course_code): whether the student has met the prerequisites for one course.
- search_sections(course_code, earliest_start): sections of one course, optionally only those starting at or after a time.
- build_schedule(student_id, section_ids): builds the schedule. It only rejects unknown sections and time conflicts. It does not check prerequisites or the user's time rules.

A separate validator checked the final schedule. Its verdict is given below.

Failure types. Pick exactly one:
- wrong_tool: the agent called the wrong tool for the step.
- invalid_arguments: the arguments did not match the tool's schema.
- wrong_argument_values: the arguments were well formed but had the wrong values.
- goal_not_met: the action ignored part of what the user asked for.
- tool_failure: the call was right but the tool returned a wrong or failed result.
- wasted_steps: the step repeated work and wasted the step budget.

How to decide:
- Compare each step's arguments with what earlier steps returned and with what the agent was evidently trying to do.
- Compare each tool output with what the other outputs in the trace imply.
- Name the earliest step that, had it been done correctly, would most likely have led to a valid schedule.
- If you cannot find such a step, set predicted_step to null.

{checks_section}User request:
{user_request}

Trace, one JSON object per step:
{trace}

Run outcome: {outcome}
Validator reasons:
{validator_reasons}

Reply with JSON only:
- predicted_step: the step number, or null.
- failure_type: one of the six types above, or null if predicted_step is null.
- reason: one sentence.
- evidence: a list of objects with step_number, field (for example "arguments.course_code" or "output.met"), and note.
