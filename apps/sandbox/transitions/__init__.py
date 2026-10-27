"""One pure transition function for committed execution and future preview."""

from dataclasses import dataclass, replace

from apps.sandbox.allocator import allocate
from apps.sandbox.state import SandboxState


READ_ONLY_TOOLS = ("get_service_status", "get_service_config")


@dataclass(frozen=True)
class Action:
    name: str
    environment: str
    service: str
    key: str | None = None
    value: int | None = None
    migration_id: str | None = None
    approval_id: str | None = None

    def __post_init__(self) -> None:
        if self.name not in ("set_service_config", "restart_service", "apply_migration", *READ_ONLY_TOOLS):
            raise ValueError("unknown tool")
        if self.environment not in ("staging", "production"):
            raise ValueError("unknown environment")
        if not isinstance(self.service, str) or not self.service:
            raise ValueError("service must be a nonempty string")
        if self.name == "set_service_config":
            if self.key != "connection_limit" or type(self.value) is not int or self.value < 1 or self.migration_id is not None or self.approval_id is not None:
                raise ValueError("only connection_limit with a positive integer value is supported")
        elif self.name == "restart_service" or self.name in READ_ONLY_TOOLS:
            if any(value is not None for value in (self.key, self.value, self.migration_id, self.approval_id)):
                raise ValueError(f"{self.name} accepts no configuration or approval arguments")
        elif (
            self.key is not None or self.value is not None
            or self.migration_id != "checkout-index-v1"
            or type(self.approval_id) is not str or not self.approval_id
        ):
            raise ValueError("apply_migration requires the known synthetic migration and an approval ID")


def parse_action(proposal: dict) -> Action:
    if type(proposal) is not dict:
        raise ValueError("tool proposal must be an object")
    return Action(**proposal)


def transition(state: SandboxState, action: Action) -> SandboxState:
    """Config changes pending state only; restart activates it and reallocates."""
    target = getattr(state, action.environment)
    if action.service != target.service:
        raise ValueError("unknown service in target environment")
    if action.name in READ_ONLY_TOOLS:
        return state
    if action.name == "set_service_config":
        return replace(state, **{action.environment: replace(target, pending_connection_limit=action.value)}, version=state.version + 1)
    if action.name == "apply_migration":
        return replace(state, **{action.environment: replace(target, schema_revision=max(target.schema_revision, 1))}, version=state.version + 1)

    activated = replace(target, active_connection_limit=target.pending_connection_limit, allocated_connections=0)
    updated = replace(state, **{action.environment: activated})
    production, staging = allocate(updated.total_capacity, updated.production.active_connection_limit, updated.staging.active_connection_limit, action.environment)
    return replace(
        updated,
        production=replace(updated.production, allocated_connections=production),
        staging=replace(updated.staging, allocated_connections=staging),
        version=state.version + 1,
    )
