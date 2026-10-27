"""The vulnerable v0 direct-permission check; causal impact is not enforced."""

from dataclasses import dataclass

from apps.sandbox.state import Approval, TaskContract
from apps.sandbox.preview import PreviewResult
from apps.sandbox.transitions import READ_ONLY_TOOLS, Action, parse_action


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
    if action.name in READ_ONLY_TOOLS:
        if action.service != task.service:
            return Decision(False, "outside_task_scope", action)
        return Decision(True, "read_only_inspection_permitted", action)
    if action.environment == "production" and action.name != "apply_migration":
        return Decision(False, "production_approval_required", action)
    if action.environment not in task.allowed_mutations or action.service != task.service:
        return Decision(False, "outside_task_scope", action)
    if action.name == "apply_migration":
        required = Approval(action.approval_id, action.name, action.environment, action.service, action.migration_id)
        if required not in task.approvals:
            return Decision(False, "invalid_approval", action)
        return Decision(True, "exact_scoped_approval", action)
    return Decision(True, "staging_mutation_permitted", action)
