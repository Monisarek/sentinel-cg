"""Immutable sandbox snapshots and trusted task requirements."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json


def _integer(name: str, value: int, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class ServiceState:
    service: str
    active_connection_limit: int
    pending_connection_limit: int
    minimum_healthy_connections: int
    allocated_connections: int

    def __post_init__(self) -> None:
        if not isinstance(self.service, str) or not self.service:
            raise ValueError("service must be a nonempty string")
        for name in ("active_connection_limit", "pending_connection_limit", "allocated_connections"):
            _integer(name, getattr(self, name))
        _integer("minimum_healthy_connections", self.minimum_healthy_connections, 1)
        if self.allocated_connections > self.active_connection_limit:
            raise ValueError("allocation exceeds the active request")

    @property
    def health(self) -> str:
        return "HEALTHY" if self.allocated_connections >= self.minimum_healthy_connections else "DEGRADED"


@dataclass(frozen=True)
class SandboxState:
    pool_id: str
    total_capacity: int
    production: ServiceState
    staging: ServiceState
    version: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.pool_id, str) or not self.pool_id:
            raise ValueError("pool_id must be a nonempty string")
        _integer("total_capacity", self.total_capacity, 1)
        _integer("version", self.version)
        if self.production.allocated_connections + self.staging.allocated_connections > self.total_capacity:
            raise ValueError("allocations exceed shared capacity")

    @classmethod
    def from_dict(cls, data: dict) -> "SandboxState":
        return cls(**{**data, "production": ServiceState(**data["production"]), "staging": ServiceState(**data["staging"])})


@dataclass(frozen=True)
class TaskContract:
    service: str
    production_minimum: int
    staging_minimum: int
    allowed_mutations: tuple[str, ...] = ("staging",)

    def __post_init__(self) -> None:
        if not isinstance(self.service, str) or not self.service:
            raise ValueError("task service must be a nonempty string")
        _integer("production_minimum", self.production_minimum, 1)
        _integer("staging_minimum", self.staging_minimum, 1)
        if type(self.allowed_mutations) is not tuple or any(e not in ("production", "staging") for e in self.allowed_mutations):
            raise ValueError("allowed_mutations must contain known environments")


def encode_state(state: SandboxState) -> str:
    return json.dumps(asdict(state), sort_keys=True, separators=(",", ":"))


def state_hash(state: SandboxState) -> str:
    return sha256(encode_state(state).encode("utf-8")).hexdigest()
