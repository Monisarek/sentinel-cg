import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { normalizeEvidence, downloadUrl, sourceLabel } from "../src/evidence.ts";

// Compile the existing component in memory; the browser still uses Vite normally.
registerHooks({
  resolve(specifier, context, nextResolve) {
    return nextResolve(specifier === "./evidence" ? "./evidence.ts" : specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith(".css")) return { format: "module", source: "export {};", shortCircuit: true };
    if (url.endsWith(".tsx")) return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText };
    return nextLoad(url, context);
  },
});
const { Workspace } = await import("../src/main.tsx");
const render = (raw) => renderToStaticMarkup(createElement(Workspace, { bundle: normalizeEvidence(raw), selected: 1, select() {}, recorded: 0 }));

// Protocol-shaped UI test data only. These objects are never AI evidence.
const state = (production = 8, staging = 2, version = 0) => ({
  total_capacity: 12, version,
  production: { service: "checkout-api", allocated_connections: production, active_connection_limit: 8,
    pending_connection_limit: 8, minimum_healthy_connections: 8, health: production < 8 ? "DEGRADED" : "HEALTHY" },
  staging: { service: "checkout-api", allocated_connections: staging, active_connection_limit: staging,
    pending_connection_limit: staging, minimum_healthy_connections: 4, health: staging < 4 ? "DEGRADED" : "HEALTHY" },
});
const action = { name: "restart_service", environment: "staging", service: "checkout-api" };
const patch = { added_rules: [{ id: "test-capacity-guard", effect: "deny", conditions: [
  { left: "impact.projected_production_allocation", op: "lt_field", right: "impact.production_minimum_required" },
] }] };
const verification = { status: "PASS", checks: { original_counterexample_blocked: true, deny_preserves_state: true,
  "regression-canonical-utility-candidate": true, "heldout-case-utility-candidate": true },
  case_counts: { regression: 1, heldout: 1 }, runs: { "regression-canonical-utility-candidate": {
    committed_states: [state(), state(8, 4, 2)], safety: { safe: true }, utility: { completed: true },
  } } };
const receipt = { promoted: true, active_version: "v1", policy_hash: "test-v1-hash", reason: "verification_passed" };
const call = (role) => ({ provenance: { provider: "meta", model_id: `unit-meta-${role}`,
  real_model_call: false, evaluation_status: "deterministic_test_fixture", response_id: `test-${role}-response`,
  prompt_hash: "test-prompt-hash", usage: { prompt_tokens: 12, completion_tokens: 3 } } });
function replay() {
  const event = (decision, after) => ({ action, decision, outcome: decision === "DENY" ? "NOT_EXECUTED" : "EXECUTED",
    reason: "deterministic_test_fixture", actual_delta: [], after_hash: `test-${decision}-state`,
    before_hash: "test-before-hash", policy: decision === "DENY" ? "v1" : "v0",
    safety: { safe: after.production.allocated_connections >= 8, violations: [] } });
  const fork = (decision, after) => ({ events: [event(decision, after)], committed_states: [state(), after],
    final_state_hash: "test-final-hash", safety: { safe: after.production.allocated_connections >= 8, violations: [] },
    utility: { completed: false, failures: [] } });
  return { status: "PASS", recorded_trace: [action], forks: { v0: fork("ALLOW", state(6, 6, 1)), v1: fork("DENY", state()) },
    first_divergence: { step: 1 }, policy_hash_v0: "test-v0-hash", policy_hash_v1: "test-v1-hash", source_trace_hash: "test-trace-hash" };
}
function runtime() {
  return { run_id: "ui-meta-test-fixture", evidence_kind: "deterministic_test_fixture", execution_mode: "meta_policy_repair_cycle",
    provider: "meta", real_model_call: false, status: "PASS", runtime_status: "COMPLETED", initial_state_hash: "test-initial-hash",
    gates: { G3: "PENDING", G4_AI: "PENDING", G5: "PASS", G6: "PASS", G7_AI: "PENDING" },
    runtime_checks: { verified_discovery: true, validated_synthesis: true, fresh_re_attack_completed: true },
    stages: { red: { seed: 0, initial_state_hash: "test-initial-hash", model_calls: [call("red")], agent_decisions: [{ kind: "action", action, explanation: "Deterministic UI fixture." }],
      policy_decisions: [replay().forks.v0.events[0]], verified_counterexample: true, oracle_verdicts: { safety: { safe: false } } },
      blue: { model_calls: [call("blue")], candidate_patch: patch }, verification: { ...receipt, status: "PASS", evaluation: verification },
      replay: replay(), reattack: { model_calls: [call("reattack")], result: "No Verified Counterexample Found Within Evaluation Budget",
        seed: 1, initial_state_hash: "test-fresh-hash", inference_status: "COMPLETED", verified_counterexample: false,
        policy_decisions: [{ ...replay().forks.v1.events[0], outcome: "DENIED" }], oracle_verdicts: { safety: { safe: true } } } },
  };
}

test("offline evidence keeps the same recorded replay and independent fixture results", () => {
  const raw = { run_id: "ui-offline-test-fixture", evidence_kind: "deterministic_test_fixture", checks: { replay: true },
    candidate: { patch, receipt, verification }, original_proposals: [action], counterfactual_replay: replay(),
    negative_control: { receipt: { ...receipt, promoted: false }, verification: { ...verification, status: "FAIL" } },
    safe_repair_after_promotion: { passed: true, final_state: state(8, 4, 2) } };
  const view = normalizeEvidence(raw);
  assert.strictEqual(view.raw, raw);
  assert.strictEqual(view.counterfactual_replay.forks, raw.counterfactual_replay.forks);
  assert.equal(view.negative_control.verification.status, "FAIL");
  assert.equal(view.safe_repair_after_promotion.final_state.production.allocated_connections, 8);
  assert.equal(view.gates.G3, "PENDING");
  assert.equal(view.model_calls.length, 0);
  assert.equal(sourceLabel(view), "Deterministic test fixture");
});

test("complete Meta runtime schema shares the same Time Machine view without relabeling fixtures as AI", () => {
  const raw = runtime();
  const before = JSON.stringify(raw);
  const view = normalizeEvidence(raw);
  assert.equal(view.format, "meta");
  assert.strictEqual(view.original_proposals, raw.stages.replay.recorded_trace);
  assert.strictEqual(view.counterfactual_replay.forks.v0, raw.stages.replay.forks.v0);
  assert.equal(view.candidate.receipt.promoted, true);
  assert.equal(view.candidate.verification.checks.deny_preserves_state, true);
  assert.equal(view.verified_utility.passed, true);
  assert.equal(view.verified_utility.final_state.staging.allocated_connections, 4);
  assert.equal(view.safe_repair_after_promotion, null);
  assert.equal(view.negative_control, null);
  assert.deepEqual(view.model_calls.map(item => item.model_id), ["unit-meta-red", "unit-meta-blue", "unit-meta-reattack"]);
  assert.equal(view.model_calls.every(item => !item.real_model_call), true);
  assert.deepEqual([view.gates.G3, view.gates.G4_AI, view.gates.G7_AI], ["PENDING", "PENDING", "PENDING"]);
  assert.equal(view.stages[0].events.length, 1);
  assert.equal(view.stages[0].oracle.safety.safe, false);
  assert.equal(JSON.stringify(raw), before);
});

test("fixture provenance suppresses falsely reported AI gate PASS", () => {
  const raw = runtime();
  raw.real_model_call = true;
  raw.gates = { G3: "PASS", G4_AI: "PASS", G7_AI: "PASS" };
  for (const key of ["red", "blue", "reattack"]) raw.stages[key].model_calls[0].provenance.real_model_call = true;
  const view = normalizeEvidence(raw);
  assert.deepEqual([view.gates.G3, view.gates.G4_AI, view.gates.G7_AI], ["PENDING", "PENDING", "PENDING"]);
  assert.equal(sourceLabel(view), "Deterministic test fixture");
});

test("AI gates cannot trust a top-level live flag without real per-stage Meta provenance", () => {
  const raw = runtime();
  // Synthetic protocol validation only: the underlying calls remain explicitly fake.
  raw.evidence_kind = "genuine_live_provider_run";
  raw.real_model_call = true;
  raw.gates = { G3: "PASS", G4_AI: "PASS", G7_AI: "PASS" };
  const view = normalizeEvidence(raw);
  assert.deepEqual([view.gates.G3, view.gates.G4_AI, view.gates.G7_AI], ["PENDING", "PENDING", "PENDING"]);
});

test("partial stopped Meta runtime preserves completed stage evidence without inventing replay or promotion", () => {
  const raw = runtime();
  raw.status = "FAIL";
  raw.runtime_status = "STOPPED";
  raw.error_category = "PROVIDER_BLOCKED";
  raw.failure = { phase: "blue", error_type: "ProviderBlocked", message: "Provider denied this test request." };
  raw.stages.blue = { model_calls: [], error_category: "PROVIDER_BLOCKED" };
  raw.stages.verification = null;
  raw.stages.replay = null;
  raw.stages.reattack = null;
  // A top-level legacy replay must never fill a missing Meta replay stage.
  raw.counterfactual_replay = replay();
  raw.original_proposals = [action];
  const view = normalizeEvidence(raw);
  assert.equal(view.counterfactual_replay, null);
  assert.deepEqual(view.original_proposals, []);
  assert.equal(view.candidate.patch, null);
  assert.equal(view.candidate.receipt, null);
  assert.equal(view.candidate.verification, null);
  assert.equal(view.stages[0].events.length, 1);
  assert.equal(view.stages[1].status, "STOPPED");
  assert.equal(view.stages[4].status, "PENDING");
  assert.ok(view.errors.includes("PROVIDER_BLOCKED"));
  assert.ok(view.errors.includes("Provider denied this test request."));
});

test("no-key or budget-blocked attempt renders statuses and reasons without fake model IDs", () => {
  const view = normalizeEvidence({ run_id: "ui-blocked-fixture", execution_mode: "meta_policy_repair_cycle",
    evidence_kind: "meta_runtime_attempt", status: "FAIL", runtime_status: "BLOCKED", provider: "meta", models: { red: "requested-only" },
    blocked_reasons: ["MODEL_API_KEY_MISSING", "BUDGET_NOT_AUTHORIZED"], stages: {},
    authorization_error: { error_type: "BudgetNotAuthorized", message: "No budget has been authorized." } });
  assert.equal(view.counterfactual_replay, null);
  assert.equal(view.model_calls.length, 0);
  assert.equal(view.stages.every(item => item.status === "PENDING"), true);
  assert.equal(view.gates.G3, "PENDING");
  assert.equal(view.gates.G4_AI, "PENDING");
  assert.equal(view.gates.G7_AI, "PENDING");
  assert.ok(view.errors.includes("MODEL_API_KEY_MISSING"));
  assert.equal(sourceLabel(view), "Meta runtime attempt");
});

test("checkpointed actions remain inspectable while an inference stage is in progress", () => {
  const raw = runtime();
  raw.stages.red.in_progress = true;
  raw.stages.replay = null;
  const view = normalizeEvidence(raw);
  assert.equal(view.stages[0].status, "IN PROGRESS");
  assert.equal(view.stages[0].decisions[0].action.name, "restart_service");
  assert.equal(view.counterfactual_replay, null);
});

test("a failed phase is STOPPED even when only diagnostic calls were checkpointed", () => {
  const raw = runtime();
  raw.runtime_status = "STOPPED";
  raw.error_category = "SCHEMA_ERROR";
  raw.failure = { phase: "blue", error_type: "ProviderSchemaError", message: "The test candidate did not match the schema." };
  raw.stages.blue = { model_calls: [], inference_diagnostics: [{ status: "SCHEMA_ERROR" }] };
  const view = normalizeEvidence(raw);
  assert.equal(view.stages[1].status, "STOPPED");
  assert.match(view.stages[1].error, /SCHEMA_ERROR/);
  assert.equal(view.gates.G4_AI, "PENDING");
});

test("an incomplete or malformed replay cannot produce displayable committed worlds", () => {
  const raw = runtime();
  raw.stages.replay.forks.v1.committed_states.pop();
  const view = normalizeEvidence(raw);
  assert.equal(view.counterfactual_replay, null);
  assert.deepEqual(view.original_proposals, []);
  assert.match(view.replay_notice, /two complete committed-state traces/);
  assert.notEqual(view.stages[3].raw, null);
});

test("download uses the original run endpoint and preserves the raw object", () => {
  const raw = runtime();
  raw.run_id = "test fixture/with spaces";
  const view = normalizeEvidence(raw);
  assert.equal(downloadUrl(view), "/api/runs/test%20fixture%2Fwith%20spaces/download");
  assert.strictEqual(view.raw, raw);
});

test("invalid bundle objects fail clearly", () => {
  for (const value of [null, [], {}, { run_id: "unknown" }]) assert.throws(() => normalizeEvidence(value), /unsupported Evidence Bundle/);
});

test("the complete Meta fixture renders the existing Time Machine and recorded call details", () => {
  const html = render(runtime());
  assert.match(html, /Same actions/);
  assert.match(html, /Policy v0 committed state/);
  assert.match(html, /Policy v1 committed state/);
  assert.match(html, /unit-meta-red/);
  assert.match(html, /unit-meta-blue/);
  assert.match(html, /test-capacity-guard/);
  assert.match(html, /regression-canonical-utility-candidate/);
  assert.match(html, /Deterministic test fixtures do not establish autonomous discovery/);
  assert.doesNotMatch(html, /Handcrafted candidate/);
});

test("a partial Meta fixture renders actual completed actions and stop details without Time Machine worlds", () => {
  const raw = runtime();
  raw.status = "FAIL";
  raw.runtime_status = "STOPPED";
  raw.error_category = "PROVIDER_BLOCKED";
  raw.failure = { phase: "blue", error_type: "ProviderBlocked", message: "Provider stopped this deterministic test." };
  raw.stages.blue = null;
  raw.stages.verification = null;
  raw.stages.replay = null;
  raw.stages.reattack = null;
  const html = render(raw);
  assert.match(html, /PROVIDER_BLOCKED/);
  assert.match(html, /STOPPED/);
  assert.match(html, /unit-meta-red/);
  assert.match(html, /Actual Gateway tool calls/);
  assert.match(html, /restart_service/);
  assert.match(html, /No candidate patch has been recorded/);
  assert.doesNotMatch(html, /Policy v0 committed state/);
  assert.doesNotMatch(html, /PROMOTED TO/);
});

test("a fresh verified bypass cannot appear as overall PASS in a legacy or new bundle", () => {
  const raw = runtime();
  raw.status = "PASS";
  raw.stages.reattack.verified_counterexample = true;
  raw.stages.reattack.oracle_verdicts.safety.safe = false;
  // A stale declared assurance cannot override the committed-state counterexample.
  raw.policy_assurance_status = "NO_COUNTEREXAMPLE_FOUND_WITHIN_BUDGET";
  const view = normalizeEvidence(raw);
  assert.equal(view.status, "FAIL");
  assert.equal(view.pipeline_status, "COMPLETED");
  assert.equal(view.policy_assurance_status, "BYPASSED");
  assert.equal(view.re_attack_outcome, "NEW_VERIFIED_COUNTEREXAMPLE_FOUND");
  assert.equal(view.gates.G7_AI, "PENDING");
  const html = render(raw);
  assert.match(html, /BYPASSED/);
  assert.match(html, /A fresh re-attack found a verified counterexample/);
  assert.match(html, /Fixture PASS describes the deterministic test pipeline only/);
});

test("a bounded substantive denied attempt has a limited no-finding result", () => {
  const view = normalizeEvidence(runtime());
  assert.equal(view.status, "PASS");
  assert.equal(view.policy_assurance_status, "NO_COUNTEREXAMPLE_FOUND_WITHIN_BUDGET");
  assert.equal(view.re_attack_outcome, "NO_VERIFIED_COUNTEREXAMPLE_FOUND_WITHIN_EVALUATION_BUDGET");
  assert.equal(view.gates.G7_AI, "PENDING");
});

test("empty, read-only, and budget-failed fresh attempts remain inconclusive", () => {
  for (const mode of ["empty", "read_only", "budget_exceeded", "budget_check_error", "validation_error", "preview_error", "policy_error"]) {
    const raw = runtime();
    if (mode === "empty") raw.stages.reattack.policy_decisions = [];
    else if (mode === "read_only") raw.stages.reattack.policy_decisions[0].action = { ...action, name: "get_service_status" };
    else raw.stages.reattack.policy_decisions[0].reason = mode;
    const view = normalizeEvidence(raw);
    assert.equal(view.status, "FAIL", mode);
    assert.equal(view.policy_assurance_status, "INCONCLUSIVE", mode);
    assert.equal(view.re_attack_outcome, "INCONCLUSIVE", mode);
    assert.equal(view.gates.G7_AI, "PENDING", mode);
  }
});
