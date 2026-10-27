"""One pure transition function for committed execution and future preview."""

from dataclasses import dataclass, replace

from apps.sandbox.allocator import allocate
from apps.sandbox.state import SandboxState


@dataclass(frozen=True)
class Action:
    name: str
    environment: str
    service: str
    key: str | None = None
    value: int | None = None

    def __post_init__(self) -> None:
        if self.name not in ("set_service_config", "restart_service"):
            raise ValueError("unknown tool")
        if self.environment not in ("staging", "production"):
            raise ValueError("unknown environment")
        if not isinstance(self.service, str) or not self.service:
            raise ValueError("service must be a nonempty string")
        if self.name == "set_service_config":
            if self.key != "connection_limit" or type(self.value) is not int or self.value < 1:
                raise ValueError("only connection_limit with a positive integer value is supported")
        elif self.key is not None or self.value is not None:
            raise ValueError("restart_service accepts no configuration arguments")


def transition(state: SandboxState, action: Action) -> SandboxState:
    """Config changes pending state only; restart activates it and reallocates."""
    target = getattr(state, action.environment)
    if action.service != target.service:
        raise ValueError("unknown service in target environment")
    if action.name == "set_service_config":
        return replace(state, **{action.environment: replace(target, pending_connection_limit=action.value)}, version=state.version + 1)

    activated = replace(target, active_connection_limit=target.pending_connection_limit, allocated_connections=0)
    updated = replace(state, **{action.environment: activated})
    production, staging = allocate(updated.total_capacity, updated.production.active_connection_limit, updated.staging.active_connection_limit, action.environment)
    return replace(
        updated,
        production=replace(updated.production, allocated_connections=production),
        staging=replace(updated.staging, allocated_connections=staging),
        version=state.version + 1,
    )
