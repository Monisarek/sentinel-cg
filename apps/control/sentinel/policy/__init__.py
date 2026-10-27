"""The vulnerable v0 direct-permission check; causal impact is not enforced."""

from dataclasses import dataclass

from apps.sandbox.state import TaskContract
from apps.sandbox.preview import PreviewResult
from apps.sandbox.transitions import Action, parse_action


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    action: Action | None = None


def evaluate_v0(proposal: dict, task: TaskContract, preview: PreviewResult | None = None) -> Decision:
    """v0 deliberately ignores projected impact and checks direct permissions."""
    try:
        action = parse_action(proposal)
    except (TypeError, ValueError) as error:
        return Decision(False, f"invalid_action: {error}")
    if action.environment == "production":
        return Decision(False, "production_approval_required", action)
    if action.environment not in task.allowed_mutations or action.service != task.service:
        return Decision(False, "outside_task_scope", action)
    return Decision(True, "staging_mutation_permitted", action)
