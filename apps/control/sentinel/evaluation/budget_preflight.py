"""Read-only configuration checks; no ledger, authorization changes, or HTTP calls."""

from decimal import Decimal
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from apps.control.sentinel.agents.budget import ApprovedBudget, Pricing, ROLES


REFERENCE = Path(__file__).resolve().parents[4] / "evals/meta_model_reference.json"


def check_meta_budget(config: Path, *, key_present=None, config_bytes: bytes | None = None) -> dict:
    data = json.loads(config_bytes if config_bytes is not None else Path(config).read_bytes())
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    if type(data) is not dict:
        raise ValueError("authorization configuration must be an object")
    expected = set(ApprovedBudget.__dataclass_fields__) | {"authorized", "models", "pricing"}
    if set(data) != expected:
        raise ValueError("authorization configuration has unknown or missing fields")
    authorized = data["authorized"] is True and type(data["approval_id"]) is str and bool(data["approval_id"].strip())
    blockers = [] if authorized else ["BUDGET_NOT_AUTHORIZED"]
    # Validate caps locally; this object never leaves preflight or authorizes execution.
    values = {key: data[key] for key in ApprovedBudget.__dataclass_fields__}
    values["approval_id"] = data["approval_id"] if authorized else "UNAUTHORIZED_PREFLIGHT_ONLY"
    approval = ApprovedBudget(**values)
    models = data["models"]
    if type(models) is not dict or set(models) != {"red", "blue"} or any(type(model) is not str or not model for model in models.values()):
        raise ValueError("exact Red and Blue model IDs are required")
    if type(data["pricing"]) is not list:
        raise ValueError("pricing must contain explicit verification records")
    records = {}
    for item in data["pricing"]:
        if type(item) is not dict or type(item.get("model_id")) is not str or item["model_id"] in records:
            raise ValueError("pricing requires unique model records")
        records[item["model_id"]] = item
    rates, details = {}, {}
    for model in sorted(set(models.values())):
        record, documented = records.get(model), reference["models"].get(model)
        errors = []
        if documented is None:
            errors.append("MODEL_NOT_IN_REVIEWED_OFFICIAL_REFERENCE")
        try:
            if record is None:
                raise ValueError("missing model price and context record")
            rate = Pricing(**record)
            if urlparse(rate.verification_source).hostname != "dev.meta.ai":
                errors.append("OFFICIAL_VERIFICATION_SOURCE_REQUIRED")
            if documented is not None and any(
                Decimal(str(getattr(rate, field))) != Decimal(str(documented[field]))
                for field in ("input_usd_per_million", "output_usd_per_million", "input_token_limit")
            ):
                errors.append("PRICE_OR_CONTEXT_DIFFERS_FROM_REVIEWED_REFERENCE")
            if not errors:
                rates[model] = rate
        except (ValueError, TypeError):
            errors.append("OFFICIAL_PRICE_OR_CONTEXT_UNVERIFIED")
        details[model] = {"verification_errors": errors, "reference": documented,
                          "worst_case_request_usd": str(rates[model].cost(rates[model].input_token_limit, approval.max_completion_tokens)) if model in rates else None,
                          "reference_worst_case_usd": str((Decimal(documented["input_usd_per_million"]) * documented["input_token_limit"] + Decimal(documented["output_usd_per_million"]) * approval.max_completion_tokens) / Decimal(1_000_000)) if documented else None,
                          "requested_max_completion_tokens": approval.max_completion_tokens,
                          "maximum_output_limit_status": "UNVERIFIED"}
        blockers.extend(model + ":" + error for error in errors)
    stages = {}
    reservations = []
    for role in ROLES:
        model = models["red"] if role == "reattack" else models[role]
        cost = rates[model].cost(rates[model].input_token_limit, approval.max_completion_tokens) if model in rates else None
        constraints = []
        if cost is None:
            constraints.append("MODEL_PRICING_UNVERIFIED")
        else:
            reservations.append(cost)
            for name, cap in (("stage_usd", approval.stage_usd[role]), ("cycle_usd", approval.cycle_usd),
                              ("daily_usd", approval.daily_usd), ("total_usd", approval.total_usd)):
                if cost > cap:
                    constraints.append(name.upper() + "_EXCEEDED")
            if approval.max_completion_tokens > rates[model].input_token_limit:
                constraints.append("COMPLETION_CAP_EXCEEDS_CONTEXT_WINDOW")
        stages[role] = {"model_id": model, "worst_case_request_usd": str(cost) if cost is not None else None,
                        "stage_cap_usd": str(approval.stage_usd[role]), "constraints": constraints,
                        "reference_request_usd": details[model]["reference_worst_case_usd"],
                        "reference_within_stage_cap": Decimal(details[model]["reference_worst_case_usd"]) <= approval.stage_usd[role] if details[model]["reference_worst_case_usd"] is not None else None,
                        "one_request_feasible": not constraints}
        blockers.extend(role + ":" + item for item in constraints)
    minimum = sum(reservations, Decimal(0)) if len(reservations) == len(ROLES) else None
    for name in ("cycle_usd", "daily_usd", "total_usd"):
        if minimum is not None and minimum > getattr(approval, name):
            blockers.append("ONE_REQUEST_PER_STAGE_" + name.upper() + "_EXCEEDED")
    if approval.cycle_calls < len(ROLES):
        blockers.append("ONE_REQUEST_PER_STAGE_CYCLE_CALLS_EXCEEDED")
    budget_feasible = not any(item != "BUDGET_NOT_AUTHORIZED" for item in blockers)
    has_key = bool(os.getenv("MODEL_API_KEY", "").strip()) if key_present is None else key_present is True
    if not has_key:
        blockers.append("MODEL_API_KEY_MISSING")
    caps = approval.to_dict()
    caps.pop("approval_id")
    return {"schema_version": 1, "status": "PASS" if not blockers else "FAIL",
            "scope": "local_zero_network_budget_preflight", "authorized": authorized,
            "api_key_present": has_key, "budget_feasible": budget_feasible,
            "models": details, "stages": stages, "caps": caps,
            "minimum_one_request_per_stage_usd": str(minimum) if minimum is not None else None,
            "blockers": blockers, "reference_reviewed_at": reference["reviewed_at"],
            "sources": reference["sources"], "model_calls": 0,
            "unverified": ["Account entitlement and credits", "Numeric model output ceilings", "Actual API acceptance and latency", "Current remaining ledger balances"],
            "limitations": ["This command never authorizes spending or modifies configuration.",
                            "Budget feasibility assumes an empty ledger; runtime also checks existing reservations and spending.",
                            "One feasible request per stage does not guarantee a complete discovery/repair cycle."]}
