"""Cost accounting. This is the only place recovery cost is added up."""

from pydantic import BaseModel

from agent_replay.models import StoredRun


class RecoveryCost(BaseModel):
    localization_tokens: int
    repair_tokens: int
    replay_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.localization_tokens + self.repair_tokens + self.replay_tokens


def recovery_cost(recovery_run: StoredRun | None, localization_tokens: int) -> RecoveryCost:
    """Split a recovery into its three parts.

    Repair is the one model call that regenerates the branch step. A forced
    action has no model call, so its repair cost is zero. Replay is every model
    call after that. Reused steps cost nothing: they were never re-called.
    A full restart has no branch step, so all of it is replay.
    """
    repair = replay = 0
    if recovery_run is not None:
        for step in recovery_run.steps:
            if step.origin == "reused":
                continue
            if recovery_run.kind == "continuation" and step.step_number == recovery_run.branch_step:
                repair += step.tokens
            else:
                replay += step.tokens
    return RecoveryCost(localization_tokens=localization_tokens, repair_tokens=repair, replay_tokens=replay)
