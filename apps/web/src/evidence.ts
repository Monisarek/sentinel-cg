/** Normalize recorded evidence for presentation without changing its source bundle. */
export type JsonRecord = Record<string, unknown>;
export type Service = {
  service: string; allocated_connections: number; active_connection_limit: number;
  pending_connection_limit: number; minimum_healthy_connections: number; health: string;
};
export type Snapshot = { total_capacity: number; production: Service; staging: Service; version: number };
export type Proposal = { name: string; environment: string; service: string; key?: string; value?: number };
export type Delta = { resource: string; field: string; before: number | string; after: number | string };
export type GatewayEvent = {
  action: Proposal; decision: string; outcome: string; reason: string; actual_delta: Delta[];
  after_hash: string; safety: { safe: boolean; violations: string[] };
};
export type Fork = {
  events: GatewayEvent[]; committed_states: Snapshot[]; final_state_hash: string;
  safety: { safe: boolean; violations: string[] }; utility: { completed: boolean; failures: string[] };
};
export type Condition = { left: string; op: string; right?: string | null; value?: string | boolean | null };
export type Patch = { added_rules: { id: string; effect: string; conditions: Condition[] }[] };
export type Verification = { status: string; checks: Record<string, boolean>; case_counts: { regression: number; heldout: number } };
export type Receipt = { promoted: boolean; active_version: string; policy_hash: string; reason: string };
export type Replay = {
  status: string; forks: Record<"v0" | "v1", Fork>; first_divergence: { step: number } | null;
  policy_hash_v0: string; policy_hash_v1: string; source_trace_hash: string;
};
export type RecordedCall = {
  stage: string; provider: string; model_id: string; real_model_call: boolean;
  evaluation_status: string; response_id: string; prompt_hash: string; usage: JsonRecord;
};
export type Stage = {
  key: string; title: string; status: string; result: string; error: string;
  calls: RecordedCall[]; decisions: JsonRecord[]; events: JsonRecord[];
  oracle: JsonRecord; raw: JsonRecord | null;
};
export type Bundle = {
  raw: JsonRecord; format: "meta" | "offline"; fixture: boolean;
  run_id: string; status: string; runtime_status: string; evidence_kind: string; real_model_call: boolean;
  project_git_commit: string; working_tree_dirty: boolean; initial_state_hash: string; bundle_hash: string;
  provider: string; original_proposals: Proposal[]; gates: Record<string, string>;
  candidate: { patch: Patch | null; receipt: Receipt | null; verification: Verification | null };
  negative_control: { receipt: Receipt; verification: Verification | null } | null;
  safe_repair_after_promotion: { passed: boolean; final_state: Snapshot } | null;
  verified_utility: { passed: boolean; final_state: Snapshot; source: string } | null;
  counterfactual_replay: Replay | null; stages: Stage[]; model_calls: RecordedCall[];
  errors: string[]; replay_notice: string; candidate_origin: string;
};

const object = (value: unknown): JsonRecord => value !== null && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {};
const records = (value: unknown): JsonRecord[] => Array.isArray(value) ? value.map(object) : [];
const text = (value: unknown, fallback = ""): string => typeof value === "string" ? value : fallback;
const checks = (value: unknown): Record<string, boolean> => Object.fromEntries(Object.entries(object(value)).filter((entry): entry is [string, boolean] => typeof entry[1] === "boolean"));

function snapshot(value: unknown): value is Snapshot {
  const state = object(value);
  return typeof state.total_capacity === "number" && typeof state.version === "number"
    && [state.production, state.staging].every(service => {
      const item = object(service);
      return typeof item.service === "string" && typeof item.health === "string"
        && ["allocated_connections", "active_connection_limit", "pending_connection_limit", "minimum_healthy_connections"].every(key => typeof item[key] === "number");
    });
}

function proposal(value: unknown): value is Proposal {
  const action = object(value);
  return ["name", "environment", "service"].every(key => typeof action[key] === "string");
}

function patch(value: unknown): Patch | null {
  const item = object(value);
  if (!Array.isArray(item.added_rules) || !item.added_rules.every(rule => {
    const entry = object(rule);
    return typeof entry.id === "string" && typeof entry.effect === "string"
      && Array.isArray(entry.conditions) && entry.conditions.every(condition => {
        const clause = object(condition);
        return typeof clause.left === "string" && typeof clause.op === "string";
      });
  })) return null;
  return item as Patch;
}

function verification(value: unknown): Verification | null {
  const item = object(value);
  if (typeof item.status !== "string" || !item.checks) return null;
  const counts = object(item.case_counts);
  return { status: item.status, checks: checks(item.checks), case_counts: {
    regression: typeof counts.regression === "number" ? counts.regression : 0,
    heldout: typeof counts.heldout === "number" ? counts.heldout : 0,
  } };
}

function receipt(value: unknown): Receipt | null {
  const item = object(value);
  if (typeof item.promoted !== "boolean") return null;
  return { promoted: item.promoted, active_version: text(item.active_version), policy_hash: text(item.policy_hash), reason: text(item.reason) };
}

function replay(value: unknown, trace: Proposal[]): Replay | null {
  const item = object(value);
  const forks = object(item.forks);
  const valid = [forks.v0, forks.v1].every(value => {
    const fork = object(value);
    return Array.isArray(fork.committed_states) && fork.committed_states.length === trace.length + 1
      && fork.committed_states.every(snapshot) && Array.isArray(fork.events) && fork.events.length === trace.length
      && fork.events.every(value => {
        const event = object(value);
        return proposal(event.action) && typeof event.decision === "string" && typeof event.outcome === "string"
          && typeof event.after_hash === "string" && Array.isArray(event.actual_delta) && typeof object(event.safety).safe === "boolean";
      });
  });
  if (!valid) return null;
  const divergence = object(item.first_divergence);
  return {
    status: text(item.status, "PENDING"), forks: forks as Replay["forks"],
    first_divergence: typeof divergence.step === "number" && divergence.step >= 1 && divergence.step <= trace.length ? { step: divergence.step } : null,
    policy_hash_v0: text(item.policy_hash_v0), policy_hash_v1: text(item.policy_hash_v1), source_trace_hash: text(item.source_trace_hash),
  };
}

function stage(key: string, title: string, value: unknown): Stage {
  const item = object(value);
  const calls = records(item.model_calls).map(call => {
    const source = object(call.provenance);
    return { stage: key, provider: text(source.provider, "Unrecorded"), model_id: text(source.model_id, "Unrecorded"),
      real_model_call: source.real_model_call === true, evaluation_status: text(source.evaluation_status, "Unrecorded"),
      response_id: text(source.response_id), prompt_hash: text(source.prompt_hash), usage: object(source.usage) };
  });
  const error = [item.error_category, item.error_type, item.error_message].filter(value => typeof value === "string" && value).join(" · ");
  const present = Object.keys(item).length > 0;
  return { key, title, calls, raw: present ? item : null, error,
    status: item.in_progress === true ? "IN PROGRESS" : error ? "STOPPED" : text(item.status,
      !present ? "PENDING" : item.candidate_patch ? "PROPOSED" : calls.length || records(item.policy_decisions).length ? "RECORDED" : "PENDING"),
    result: text(item.result), decisions: records(item.agent_decisions), events: records(item.policy_decisions), oracle: object(item.oracle_verdicts) };
}

/** AI gates require recorded Meta provenance in addition to a reported gate PASS. */
export function normalizeEvidence(value: unknown): Bundle {
  const raw = object(value);
  const meta = raw.execution_mode === "meta_policy_repair_cycle";
  if (typeof raw.run_id !== "string" || (!meta && (!raw.candidate || !raw.checks))) {
    throw new Error("The server returned an unsupported Evidence Bundle.");
  }
  const fixture = raw.evidence_kind === "deterministic_test_fixture";
  const stagesRaw = object(raw.stages);
  const stages = meta ? [stage("red", "Adversarial Explorer", stagesRaw.red), stage("blue", "Policy Synthesizer", stagesRaw.blue),
    stage("verification", "Independent verification", stagesRaw.verification), stage("replay", "Safety Time Machine", stagesRaw.replay),
    stage("reattack", "Fresh adaptive re-attack", stagesRaw.reattack)] : [];
  const failure = object(raw.failure);
  const stoppedStage = stages.find(item => item.key === failure.phase);
  if (raw.runtime_status === "STOPPED" && stoppedStage) {
    stoppedStage.status = "STOPPED";
    stoppedStage.error ||= [raw.error_category, failure.error_type, failure.message].filter(value => typeof value === "string" && value).join(" · ");
  }
  const calls = stages.flatMap(item => item.calls);
  const genuine = !fixture && raw.evidence_kind === "genuine_live_provider_run" && raw.provider === "meta" && raw.real_model_call === true;
  const realStage = (key: string) => {
    const current = stages.find(item => item.key === key);
    const items = current?.calls || [];
    return !!current && current.status !== "STOPPED" && current.status !== "IN PROGRESS"
      && current.raw?.evidence_kind !== "deterministic_test_fixture"
      && items.length > 0 && items.every(item => item.provider === "meta" && item.real_model_call && item.model_id !== "Unrecorded" && item.evaluation_status !== "deterministic_test_fixture");
  };
  const runtimeChecks = checks(raw.runtime_checks);
  const aiEvidence: Record<string, boolean> = {
    G3: genuine && realStage("red") && runtimeChecks.verified_discovery === true && object(stagesRaw.red).verified_counterexample === true,
    G4_AI: genuine && realStage("blue") && runtimeChecks.validated_synthesis === true && patch(object(stagesRaw.blue).candidate_patch) !== null,
    G7_AI: genuine && realStage("reattack") && runtimeChecks.fresh_re_attack_completed === true,
  };
  const gates = Object.fromEntries(Object.entries(object(raw.gates)).map(([key, value]) => [key,
    value === "PASS" && key in aiEvidence && !aiEvidence[key] ? "PENDING" : text(value, "PENDING")]));
  for (const key of Object.keys(aiEvidence)) gates[key] ??= "PENDING";
  const candidate = object(raw.candidate);
  const verificationStage = object(stagesRaw.verification);
  const evaluation = meta ? object(verificationStage.evaluation) : object(candidate.verification);
  const candidatePatch = patch(meta ? object(stagesRaw.blue).candidate_patch : candidate.patch);
  const negative = object(raw.negative_control);
  const negativeReceipt = receipt(negative.receipt);
  const postRepair = object(raw.safe_repair_after_promotion);
  const utilityRun = object(object(evaluation.runs)["regression-canonical-utility-candidate"]);
  const utilityStates = Array.isArray(utilityRun.committed_states) ? utilityRun.committed_states : [];
  const utilityFinal = utilityRun.persisted_final_state ?? utilityStates.at(-1);
  const machineRaw = meta ? object(stagesRaw.replay) : object(raw.counterfactual_replay);
  const traceRaw = meta ? machineRaw.recorded_trace : raw.original_proposals;
  const trace = Array.isArray(traceRaw) && traceRaw.every(proposal) ? traceRaw : [];
  // Meta forks and actions come exclusively from its recorded replay stage.
  const machine = replay(machineRaw, trace);
  const errors = [raw.error_category, ...Array.isArray(raw.blocked_reasons) ? raw.blocked_reasons : [],
    ...Object.values(object(raw.failure)), ...Object.values(object(raw.authorization_error))]
    .filter((value): value is string => typeof value === "string" && !!value);
  return {
    raw, format: meta ? "meta" : "offline", fixture, run_id: raw.run_id, status: text(raw.status, "PENDING"),
    runtime_status: text(raw.runtime_status, text(raw.status, "PENDING")), evidence_kind: text(raw.evidence_kind, "Unrecorded"),
    real_model_call: raw.real_model_call === true, provider: text(raw.provider, "None"),
    project_git_commit: text(raw.project_git_commit, "Unrecorded"), working_tree_dirty: raw.working_tree_dirty === true,
    initial_state_hash: text(raw.initial_state_hash), bundle_hash: text(raw.bundle_hash), original_proposals: machine ? trace : [], gates,
    candidate: { patch: candidatePatch, receipt: receipt(meta ? verificationStage : candidate.receipt), verification: verification(evaluation) },
    negative_control: negativeReceipt ? { receipt: negativeReceipt, verification: verification(negative.verification) } : null,
    safe_repair_after_promotion: snapshot(postRepair.final_state) ? { passed: postRepair.passed === true, final_state: postRepair.final_state } : null,
    verified_utility: snapshot(utilityFinal) ? { passed: object(utilityRun.safety).safe === true && object(utilityRun.utility).completed === true,
      final_state: utilityFinal, source: "regression-canonical-utility-candidate" } : null,
    counterfactual_replay: machine, stages, model_calls: calls, errors: [...new Set(errors)],
    replay_notice: Object.keys(machineRaw).length > 0 ? "The recorded replay does not contain two complete committed-state traces. Inspect its stage evidence below."
      : "Counterfactual replay has not been recorded. No comparison states are inferred from agent proposals.",
    candidate_origin: fixture ? "Deterministic test fixture" : meta ? "Recorded Meta proposal" : text(raw.candidate_origin, "Unrecorded"),
  };
}

export function sourceLabel(bundle: Bundle): string {
  if (bundle.fixture) return "Deterministic test fixture";
  if (bundle.evidence_kind === "genuine_live_provider_run" && bundle.real_model_call && bundle.model_calls.some(call => call.provider === "meta" && call.real_model_call)) return "Recorded Meta runtime";
  return bundle.format === "meta" ? "Meta runtime attempt" : "Recorded deterministic execution";
}

export function downloadUrl(bundle: Bundle): string {
  return `/api/runs/${encodeURIComponent(bundle.run_id)}/download`;
}
