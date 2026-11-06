"""Deterministic, deliberately unfair shared-capacity allocation."""


def allocate(capacity: int, production_requested: int, staging_requested: int, priority: str) -> tuple[int, int]:
    """The restarted consumer gets capacity first; the other gets the remainder.

    Return (production, staging). Requests are capped by available capacity;
    unused capacity stays idle. No connection is created beyond either request.
    """
    if any(type(n) is not int or n < 0 for n in (capacity, production_requested, staging_requested)):
        raise ValueError("capacity and requests must be nonnegative integers")
    if priority == "staging":
        staging = min(staging_requested, capacity)
        return min(production_requested, capacity - staging), staging
    if priority == "production":
        production = min(production_requested, capacity)
        return production, min(staging_requested, capacity - production)
    raise ValueError("unknown priority consumer")
