"""Deterministic transition facts; no persistence or safety verdicts."""

from dataclasses import asdict, dataclass

from apps.sandbox.state import SandboxState, state_hash
from apps.sandbox.transitions import Action, transition


@dataclass(frozen=True)
class Effect:
    resource: str
    field: str
    before: int | str
    after: int | str


@dataclass(frozen=True)
class Impact:
    projected_production_allocation: int
    production_minimum_required: int
    projected_staging_allocation: int
    staging_minimum_required: int
    total_capacity: int
    projected_used_capacity: int
    projected_free_capacity: int
    projected_oversubscription: int


@dataclass(frozen=True)
class PreviewResult:
    action: Action
    state_version: int
    source_state_hash: str
    projected_state: SandboxState
    projected_state_hash: str
    effects: tuple[Effect, ...]
    impact: Impact


def _resource_facts(state: SandboxState) -> dict[str, dict[str, int | str]]:
    resources = {}
    for environment in ("staging", "production"):
        service = getattr(state, environment)
        resources[f"{environment}/{service.service}"] = {
            **asdict(service), "health": service.health,
        }
    used = state.production.allocated_connections + state.staging.allocated_connections
    demand = state.production.active_connection_limit + state.staging.active_connection_limit
    resources[f"shared_pool/{state.pool_id}"] = {
        "total_capacity": state.total_capacity,
        "active_demand": demand,
        "used_capacity": used,
        "free_capacity": state.total_capacity - used,
        "oversubscription": max(0, demand - state.total_capacity),
    }
    resources["sandbox"] = {"version": state.version}
    return resources


def state_delta(before: SandboxState, after: SandboxState) -> tuple[Effect, ...]:
    previous, current = _resource_facts(before), _resource_facts(after)
    return tuple(
        Effect(resource, field, value, current[resource][field])
        for resource, fields in previous.items()
        for field, value in fields.items()
        if value != current[resource][field]
    )


def impact_facts(projected: SandboxState) -> Impact:
    used = projected.production.allocated_connections + projected.staging.allocated_connections
    demand = projected.production.active_connection_limit + projected.staging.active_connection_limit
    return Impact(
        projected_production_allocation=projected.production.allocated_connections,
        production_minimum_required=projected.production.minimum_healthy_connections,
        projected_staging_allocation=projected.staging.allocated_connections,
        staging_minimum_required=projected.staging.minimum_healthy_connections,
        total_capacity=projected.total_capacity,
        projected_used_capacity=used,
        projected_free_capacity=projected.total_capacity - used,
        projected_oversubscription=max(0, demand - projected.total_capacity),
    )


def preview_action(state: SandboxState, action: Action) -> PreviewResult:
    projected = transition(state, action)
    return PreviewResult(
        action=action,
        state_version=state.version,
        source_state_hash=state_hash(state),
        projected_state=projected,
        projected_state_hash=state_hash(projected),
        effects=state_delta(state, projected),
        impact=impact_facts(projected),
    )
