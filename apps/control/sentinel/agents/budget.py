"""Explicit spending authorization with persistent, pre-request reservations."""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
import time
from types import MappingProxyType
from urllib.parse import urlparse
from uuid import uuid4

from apps.control.sentinel.agents.contracts import ProviderError


ROLES = ("red", "blue", "reattack")


class BudgetExceeded(ProviderError):
    error_category = "BUDGET_EXCEEDED"


def _money(value) -> Decimal:
    if type(value) not in (str, int, Decimal):
        raise ValueError("USD values must be exact decimal strings or Decimal values")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError("invalid USD amount") from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError("USD amounts must be finite and positive")
    return amount


def _positive_integer(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(name + " must be a positive integer")
    return value


def _utc_day():
    return datetime.now(timezone.utc).date().isoformat()


@dataclass(frozen=True)
class Pricing:
    model_id: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    input_token_limit: int
    verification_source: str
    verified_at: str

    def __post_init__(self):
        if type(self.model_id) is not str or not self.model_id.strip():
            raise ValueError("pricing requires an exact model ID")
        for field in ("input_usd_per_million", "output_usd_per_million"):
            object.__setattr__(self, field, _money(getattr(self, field)))
        _positive_integer(self.input_token_limit, "verified model context limit")
        if type(self.verification_source) is not str:
            raise ValueError("pricing requires an HTTPS verification source")
        source = urlparse(self.verification_source)
        if source.scheme != "https" or not source.netloc or source.username or source.password:
            raise ValueError("pricing requires an HTTPS verification source")
        try:
            verified = datetime.fromisoformat(self.verified_at.replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("pricing requires an ISO verification date") from None
        if verified.date() > datetime.now(timezone.utc).date():
            raise ValueError("pricing verification cannot be dated in the future")

    def cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        return (self.input_usd_per_million * prompt_tokens
                + self.output_usd_per_million * completion_tokens) / Decimal(1_000_000)

    def to_dict(self):
        return {"model_id": self.model_id, "input_usd_per_million": str(self.input_usd_per_million),
                "output_usd_per_million": str(self.output_usd_per_million),
                "input_token_limit": self.input_token_limit,
                "verification_source": self.verification_source, "verified_at": self.verified_at}


@dataclass(frozen=True)
class ApprovedBudget:
    approval_id: str
    stage_usd: dict
    cycle_usd: Decimal
    daily_usd: Decimal
    total_usd: Decimal
    stage_calls: dict
    cycle_calls: int
    stage_seconds: dict
    cycle_seconds: int
    max_completion_tokens: int

    def __post_init__(self):
        if type(self.approval_id) is not str or not self.approval_id.strip():
            raise ValueError("paid execution requires an explicit approval ID")
        for name in ("stage_usd", "stage_calls", "stage_seconds"):
            values = getattr(self, name)
            if type(values) is not dict or set(values) != set(ROLES):
                raise ValueError(name + " must specify red, blue, and reattack")
            checked = {role: _money(value) if name == "stage_usd" else _positive_integer(value, name)
                       for role, value in values.items()}
            object.__setattr__(self, name, MappingProxyType(checked))
        for name in ("cycle_usd", "daily_usd", "total_usd"):
            object.__setattr__(self, name, _money(getattr(self, name)))
        for name in ("cycle_calls", "cycle_seconds", "max_completion_tokens"):
            _positive_integer(getattr(self, name), name)

    def to_dict(self):
        return {"approval_id": self.approval_id,
                "stage_usd": {role: str(value) for role, value in self.stage_usd.items()},
                **{name: str(getattr(self, name)) for name in ("cycle_usd", "daily_usd", "total_usd")},
                "stage_calls": dict(self.stage_calls), "cycle_calls": self.cycle_calls,
                "stage_seconds": dict(self.stage_seconds), "cycle_seconds": self.cycle_seconds,
                "max_completion_tokens": self.max_completion_tokens}


@dataclass(frozen=True)
class Reservation:
    ticket_id: str
    role: str
    model_id: str
    reserved_usd: Decimal
    input_token_limit: int
    output_token_limit: int

    def to_dict(self):
        return {"ticket_id": self.ticket_id, "role": self.role, "model_id": self.model_id,
                "reserved_usd": str(self.reserved_usd), "input_token_limit": self.input_token_limit,
                "output_token_limit": self.output_token_limit}


class RuntimeBudget:
    def __init__(self, ledger_path: Path, run_id: str, approval: ApprovedBudget, pricing: list[Pricing]):
        if type(approval) is not ApprovedBudget or type(run_id) is not str or not run_id:
            raise ValueError("runtime budget requires explicit authorization and a unique run ID")
        if type(pricing) is not list or not pricing or any(type(rate) is not Pricing for rate in pricing):
            raise ValueError("runtime budget requires verified model pricing")
        if len({rate.model_id for rate in pricing}) != len(pricing):
            raise ValueError("pricing model IDs must be unique")
        self.path, self.run_id, self.approval = Path(ledger_path).resolve(), run_id, approval
        self.pricing = MappingProxyType({rate.model_id: rate for rate in pricing})
        self.started, self.stage_started = time.monotonic(), {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS budget_runs (run_id TEXT PRIMARY KEY, authorization TEXT NOT NULL, pricing TEXT NOT NULL, blocked TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS budget_calls (ticket_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, role TEXT NOT NULL, model_id TEXT NOT NULL, day TEXT NOT NULL, reserved_usd TEXT NOT NULL, accounted_usd TEXT NOT NULL, status TEXT NOT NULL, usage TEXT)")
            try:
                db.execute("INSERT INTO budget_runs VALUES (?, ?, ?, NULL)",
                           (run_id, json.dumps(approval.to_dict(), sort_keys=True),
                            json.dumps([rate.to_dict() for rate in pricing], sort_keys=True)))
            except sqlite3.IntegrityError:
                raise ValueError("a runtime budget run ID cannot be reused") from None

    def _connect(self):
        return sqlite3.connect(self.path)

    def begin_stage(self, role: str):
        if role not in ROLES:
            raise ValueError("unknown inference budget stage")
        self.stage_started.setdefault(role, time.monotonic())
        self.remaining_seconds(role)

    def remaining_seconds(self, role: str) -> float:
        if role not in ROLES:
            raise ValueError("unknown inference budget stage")
        now = time.monotonic()
        remaining = self.started + self.approval.cycle_seconds - now
        if role in self.stage_started:
            remaining = min(remaining, self.stage_started[role] + self.approval.stage_seconds[role] - now)
        if remaining <= 0:
            raise BudgetExceeded("the approved inference time limit has been reached")
        return remaining

    def reserve(self, role: str, model_id: str) -> Reservation:
        self.begin_stage(role)
        if model_id not in self.pricing:
            raise BudgetExceeded("model pricing and context limit have not been verified")
        rate = self.pricing[model_id]
        # Reserve the full verified context limit; no heuristic tokenizer bound is claimed.
        cost = rate.cost(rate.input_token_limit, self.approval.max_completion_tokens)
        ticket = Reservation(uuid4().hex, role, model_id, cost, rate.input_token_limit,
                             self.approval.max_completion_tokens)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self.remaining_seconds(role)
            if db.execute("SELECT 1 FROM budget_runs WHERE blocked IS NOT NULL LIMIT 1").fetchone():
                raise BudgetExceeded("unresolved billing verification prevents further model calls")
            rows = db.execute("SELECT run_id, role, day, accounted_usd, status FROM budget_calls").fetchall()
            current = [row for row in rows if row[0] == self.run_id]
            stage = [row for row in current if row[1] == role]
            daily = [row for row in rows if row[2] == _utc_day() or row[4] != "SETTLED"]
            for group, limit in ((rows, self.approval.total_usd), (daily, self.approval.daily_usd),
                                 (current, self.approval.cycle_usd), (stage, self.approval.stage_usd[role])):
                if sum((Decimal(row[3]) for row in group), Decimal(0)) + cost > limit:
                    raise BudgetExceeded("the approved inference spending limit would be exceeded")
            if len(current) >= self.approval.cycle_calls or len(stage) >= self.approval.stage_calls[role]:
                raise BudgetExceeded("the approved model-call limit has been reached")
            db.execute("INSERT INTO budget_calls VALUES (?, ?, ?, ?, ?, ?, ?, 'RESERVED', NULL)",
                       (ticket.ticket_id, self.run_id, role, model_id, _utc_day(), str(cost), str(cost)))
        return ticket

    def settle(self, ticket: Reservation, model_id: str, usage: dict) -> dict:
        if type(ticket) is not Reservation:
            raise ValueError("a valid pre-request reservation is required")
        valid_usage = type(usage) is dict and all(type(usage.get(key)) is int and usage[key] >= 0
                                                for key in ("prompt_tokens", "completion_tokens"))
        actual = self.pricing[model_id].cost(usage["prompt_tokens"], usage["completion_tokens"]) if valid_usage and model_id in self.pricing else None
        violation = (not valid_usage or model_id != ticket.model_id or actual is None or actual > ticket.reserved_usd
                     or usage["prompt_tokens"] > ticket.input_token_limit
                     or usage["completion_tokens"] > ticket.output_token_limit)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT run_id, role, model_id, reserved_usd, status FROM budget_calls WHERE ticket_id=?",
                             (ticket.ticket_id,)).fetchone()
            if (row is None or row[:3] != (self.run_id, ticket.role, ticket.model_id)
                    or row[4] != "RESERVED" or Decimal(row[3]) != ticket.reserved_usd
                    or ticket.input_token_limit != self.pricing[ticket.model_id].input_token_limit
                    or ticket.output_token_limit != self.approval.max_completion_tokens):
                raise BudgetExceeded("reservation is missing, already settled, or does not match this run")
            amount = max(ticket.reserved_usd, actual or Decimal(0)) if violation else actual
            usage_record = {"reported_model_id": model_id,
                            "prompt_tokens": usage["prompt_tokens"] if valid_usage else None,
                            "completion_tokens": usage["completion_tokens"] if valid_usage else None}
            db.execute("UPDATE budget_calls SET accounted_usd=?, status=?, usage=?, day=? WHERE ticket_id=?",
                       (str(amount), "UNCERTAIN" if violation else "SETTLED",
                        json.dumps(usage_record, sort_keys=True), _utc_day(), ticket.ticket_id))
            if violation:
                db.execute("UPDATE budget_runs SET blocked='BILLING_UNCERTAIN' WHERE run_id=?", (self.run_id,))
        if violation:
            raise BudgetExceeded("returned model or usage violated the verified billing bounds; reservations remain charged")
        return {"ticket_id": ticket.ticket_id, "reserved_usd": str(ticket.reserved_usd),
                "accounted_usd": str(actual), "billing_status": "SETTLED", "usage": usage_record}

    def snapshot(self) -> dict:
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            rows = db.execute("SELECT ticket_id, run_id, role, model_id, day, reserved_usd, accounted_usd, status, usage FROM budget_calls").fetchall()
            blocked = [row[0] for row in db.execute("SELECT run_id FROM budget_runs WHERE blocked IS NOT NULL")]
        current = [row for row in rows if row[1] == self.run_id]
        sum_cost = lambda selected: str(sum((Decimal(row[6]) for row in selected), Decimal(0)))
        return {"approval": self.approval.to_dict(), "pricing": [rate.to_dict() for rate in self.pricing.values()],
                "run_id": self.run_id, "reserved_calls": len(current), "accounted_usd": sum_cost(current),
                "known_cost_usd": sum_cost([row for row in current if row[7] == "SETTLED"]),
                "unresolved_reserved_usd": sum_cost([row for row in current if row[7] != "SETTLED"]),
                "daily_accounted_usd": sum_cost([row for row in rows if row[4] == _utc_day() or row[7] != "SETTLED"]),
                "total_accounted_usd": sum_cost(rows), "billing_blocked": bool(blocked),
                "cost_basis": "Conservative estimates from provider-reported tokens and verified list rates; not an invoice.",
                "stage_calls": {role: sum(row[2] == role for row in current) for role in ROLES},
                "stage_accounted_usd": {role: sum_cost([row for row in current if row[2] == role]) for role in ROLES},
                "reservations": [{"ticket_id": row[0], "role": row[2], "model_id": row[3], "day_utc": row[4],
                                  "reserved_usd": row[5], "accounted_usd": row[6], "status": row[7],
                                  "usage": json.loads(row[8]) if row[8] else None} for row in current],
                "accounting_rule": "Unresolved calls retain full reservations and count against each current UTC day until settled.",
                "input_reservation_rule": "Every call reserves the full independently verified model context input limit."}
