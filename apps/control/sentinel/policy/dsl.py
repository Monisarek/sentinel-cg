"""Bounded deny-only policy data; promotion remains an independent decision."""

from dataclasses import asdict, dataclass, fields
import operator
import re

from apps.control.sentinel.policy import Decision, evaluate_v0
from apps.sandbox.preview import Impact, PreviewResult
from apps.sandbox.state import TaskContract


FIELD_TYPES = {
    "action.name": "tool",
    "action.environment": "environment",
    "action.service": "service",
    "action.key": "key",
    "action.value": "number",
    "task.service": "service",
    "task.allowed_mutations": "environments",
    **{"impact." + field.name: "number" for field in fields(Impact)},
}
LITERAL_ENUMS = {
    "action.name": (
        "get_service_status", "get_service_config", "get_shared_pool_status",
        "set_service_config", "restart_service", "request_approval", "apply_migration",
    ),
    "action.environment": ("staging", "production"),
    "action.key": ("connection_limit",),
}
FIELD_COMPARISONS = {
    "eq_field": operator.eq, "neq_field": operator.ne,
    "lt_field": operator.lt, "lte_field": operator.le,
    "gt_field": operator.gt, "gte_field": operator.ge,
}
OPERATORS = ("eq", "neq", *FIELD_COMPARISONS, "in_field", "not_in_field", "exists")
ID_PATTERN = "^[a-z]+(?:-[a-z]+)*$"


@dataclass(frozen=True)
class Condition:
    left: str
    op: str
    right: str | None = None
    value: str | bool | None = None


@dataclass(frozen=True)
class Rule:
    id: str
    effect: str
    conditions: tuple[Condition, ...]


@dataclass(frozen=True)
class CandidatePolicyPatch:
    added_rules: tuple[Rule, ...]

    def to_dict(self) -> dict:
        return {"added_rules": [
            {"id": rule.id, "effect": rule.effect, "conditions": [asdict(condition) for condition in rule.conditions]}
            for rule in self.added_rules
        ]}


def _keys(data: dict, required: set[str], optional: set[str] = frozenset()) -> None:
    if type(data) is not dict or not required <= data.keys() or data.keys() - required - optional:
        raise ValueError("policy object has unknown or missing fields")


def _condition(data: dict) -> Condition:
    _keys(data, {"left", "op"}, {"right", "value"})
    left, op = data["left"], data["op"]
    right, value = data.get("right"), data.get("value")
    if type(left) is not str or left not in FIELD_TYPES:
        raise ValueError("unknown policy field")
    if type(op) is not str or op not in OPERATORS:
        raise ValueError("unsupported policy operator")
    if op == "exists":
        if right is not None or (value is not None and type(value) is not bool):
            raise ValueError("exists accepts only an optional boolean value")
    elif op in ("eq", "neq"):
        if right is not None or type(value) is not str or value not in LITERAL_ENUMS.get(left, ()):
            raise ValueError("literal comparison requires a known enum on an enum field")
    else:
        if type(right) is not str or right not in FIELD_TYPES or value is not None:
            raise ValueError("field comparison requires a known right field and no literal")
        if op in ("in_field", "not_in_field"):
            if FIELD_TYPES[left] != "environment" or right != "task.allowed_mutations":
                raise ValueError("membership requires an environment and task.allowed_mutations")
        elif FIELD_TYPES[left] != FIELD_TYPES[right] or FIELD_TYPES[left] == "environments":
            raise ValueError("field comparison types are incompatible")
        elif op not in ("eq_field", "neq_field") and FIELD_TYPES[left] != "number":
            raise ValueError("ordering requires numeric fields")
    return Condition(left, op, right, value)


def validate_patch(data: dict) -> CandidatePolicyPatch:
    _keys(data, {"added_rules"})
    rules = data["added_rules"]
    if type(rules) is not list or not 1 <= len(rules) <= 3:
        raise ValueError("a patch must add one to three deny rules")
    result, identifiers = [], set()
    for rule in rules:
        _keys(rule, {"id", "effect", "conditions"})
        identifier = rule["id"]
        if (
            type(identifier) is not str or not 1 <= len(identifier) <= 64
            or re.fullmatch(ID_PATTERN, identifier) is None
            or set(identifier.split("-")) & {"run", "seed", "fixture", "scenario", "approval"}
            or identifier in identifiers
        ):
            raise ValueError("rule id must be a unique, bounded lowercase label without run identifiers")
        if rule["effect"] != "deny":
            raise ValueError("only deny overlays are permitted")
        conditions = rule["conditions"]
        if type(conditions) is not list or not 1 <= len(conditions) <= 5:
            raise ValueError("a rule must contain one to five conditions")
        result.append(Rule(identifier, "deny", tuple(_condition(condition) for condition in conditions)))
        identifiers.add(identifier)
    return CandidatePolicyPatch(tuple(result))


def policy_schema() -> dict:
    """JSON schema plus descriptions; validate_patch also enforces typed operands."""
    names = sorted(FIELD_TYPES)
    literals = sorted({value for values in LITERAL_ENUMS.values() for value in values})
    return {
        "type": "object", "additionalProperties": False, "required": ["added_rules"],
        "properties": {"added_rules": {
            "type": "array", "minItems": 1, "maxItems": 3,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "effect", "conditions"],
                "properties": {
                    "id": {"type": "string", "pattern": ID_PATTERN, "maxLength": 64},
                    "effect": {"type": "string", "enum": ["deny"]},
                    "conditions": {
                        "type": "array", "minItems": 1, "maxItems": 5,
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["left", "op", "right", "value"],
                            "description": "Conditions are AND-combined; rules are OR-combined. Use null for unused right/value. Numeric comparisons must compare two numeric fields. Literal eq/neq is restricted to tool names, environments, and connection_limit. Membership right must be task.allowed_mutations. exists uses an optional boolean value (default true). Missing action fields match only exists.",
                            "properties": {
                                "left": {"type": "string", "enum": names},
                                "op": {"type": "string", "enum": list(OPERATORS)},
                                "right": {"anyOf": [{"type": "string", "enum": names}, {"type": "null"}]},
                                "value": {"anyOf": [{"type": "string", "enum": literals}, {"type": "boolean"}, {"type": "null"}]},
                            },
                        },
                    },
                },
            },
        }},
    }


def _matches(condition: Condition, context: dict) -> bool:
    def resolve(path):
        namespace, field = path.split(".")
        return getattr(context[namespace], field)

    left = resolve(condition.left)
    if condition.op == "exists":
        return (left is not None) == (True if condition.value is None else condition.value)
    if left is None:
        return False
    right = condition.value if condition.op in ("eq", "neq") else resolve(condition.right)
    if right is None:
        return False
    if condition.op == "in_field":
        return left in right
    if condition.op == "not_in_field":
        return left not in right
    if condition.op == "eq":
        return left == right
    if condition.op == "neq":
        return left != right
    return FIELD_COMPARISONS[condition.op](left, right)


class OverlayPolicy:
    def __init__(self, patch: CandidatePolicyPatch):
        if type(patch) is not CandidatePolicyPatch:
            raise ValueError("overlay requires a validated candidate patch")
        self.patch = validate_patch(patch.to_dict())

    def __call__(self, proposal: dict, task: TaskContract, preview: PreviewResult | None) -> Decision:
        base = evaluate_v0(proposal, task, preview)
        if not base.allowed:
            return base
        if type(preview) is not PreviewResult:
            return Decision(False, "overlay_preview_required", base.action)
        context = {"action": base.action, "task": task, "impact": preview.impact}
        for rule in self.patch.added_rules:
            if all(_matches(condition, context) for condition in rule.conditions):
                return Decision(False, "overlay_denied:" + rule.id, base.action)
        return base
