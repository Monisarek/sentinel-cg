import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type Service = {
  service: string; allocated_connections: number; active_connection_limit: number;
  pending_connection_limit: number; minimum_healthy_connections: number; health: string;
};
type Snapshot = { total_capacity: number; production: Service; staging: Service; version: number };
type Proposal = { name: string; environment: string; service: string; key?: string; value?: number };
type Delta = { resource: string; field: string; before: number | string; after: number | string };
type GatewayEvent = {
  action: Proposal; decision: string; outcome: string; reason: string; actual_delta: Delta[];
  after_hash: string; safety: { safe: boolean; violations: string[] };
};
type Fork = {
  events: GatewayEvent[]; committed_states: Snapshot[]; final_state_hash: string;
  safety: { safe: boolean; violations: string[] }; utility: { completed: boolean; failures: string[] };
};
type Condition = { left: string; op: string; right?: string | null; value?: string | boolean | null };
type Verification = { status: string; checks: Record<string, boolean>; case_counts: { regression: number; heldout: number } };
type Receipt = { promoted: boolean; active_version: string; policy_hash: string; reason: string };
type Bundle = {
  run_id: string; status: string; evidence_kind: string; real_model_call: boolean; project_git_commit: string;
  working_tree_dirty: boolean; initial_state_hash: string; bundle_hash: string; original_proposals: Proposal[];
  gates: Record<string, string>; checks: Record<string, boolean>;
  candidate: { patch: { added_rules: { id: string; effect: string; conditions: Condition[] }[] }; receipt: Receipt; verification: Verification };
  negative_control: { receipt: Receipt; verification: Verification | null };
  safe_repair_after_promotion: { passed: boolean; final_state: Snapshot } | null;
  counterfactual_replay: {
    status: string; forks: Record<"v0" | "v1", Fork>; first_divergence: { step: number } | null;
    policy_hash_v0: string; policy_hash_v1: string; source_trace_hash: string;
  } | null;
};

const short = (value: string) => value.slice(0, 12);
const actionTitle = (action: Proposal) => action.name === "set_service_config"
  ? `Set ${action.environment} ${action.key} = ${action.value}`
  : `${action.name.replaceAll("_", " ")} · ${action.environment}`;
const symbols: Record<string, string> = { eq: "=", neq: "≠", lt_field: "<", lte_field: "≤", gt_field: ">", gte_field: "≥", eq_field: "=", neq_field: "≠" };

async function readBundle(response: Response): Promise<Bundle> {
  if (!response.ok) throw new Error(`Evidence request failed (HTTP ${response.status}).`);
  const data = await response.json();
  if (typeof data?.run_id !== "string" || !data?.checks || !data?.candidate) throw new Error("The server returned an invalid Evidence Bundle.");
  return data as Bundle;
}

function Status({ pass, children }: { pass: boolean; children: ReactNode }) {
  return <span className={`status ${pass ? "pass" : "fail"}`}>{children}</span>;
}

function Allocation({ name, service, capacity }: { name: string; service: Service; capacity: number }) {
  const allocated = service.allocated_connections;
  const minimum = service.minimum_healthy_connections;
  return <div className="allocation">
    <div className="allocation-label"><span>{name}</span><strong>{allocated}<small> / {minimum} minimum</small></strong></div>
    <div className="capacity-track" role="meter" aria-label={`${name} allocation`} aria-valuemin={0} aria-valuemax={capacity} aria-valuenow={allocated} aria-valuetext={`${allocated} connections, required minimum ${minimum}`}>
      <div className={`capacity-fill ${allocated < minimum ? "below" : "above"}`} style={{ width: `${100 * allocated / capacity}%` }} />
      <span className="minimum-marker" style={{ left: `${100 * minimum / capacity}%` }} />
    </div>
    <div className="allocation-detail"><span>{service.health}</span><span>active {service.active_connection_limit} · pending {service.pending_connection_limit}</span></div>
  </div>;
}

function World({ version, fork, step, hash }: { version: "v0" | "v1"; fork: Fork; step: number; hash: string }) {
  const state = fork.committed_states[step];
  const event = step > 0 ? fork.events[step - 1] : null;
  const safe = event ? event.safety.safe : state.production.allocated_connections >= state.production.minimum_healthy_connections;
  const used = state.production.allocated_connections + state.staging.allocated_connections;
  return <section className={`world ${version}`} aria-label={`Policy ${version} committed state`}>
    <div className="world-heading"><div><span className="eyebrow">{version === "v0" ? "Baseline policy" : "Verified policy"}</span><h2>Policy {version}</h2></div><Status pass={safe}>{safe ? "S1 PRESERVED" : "S1 VIOLATED"}</Status></div>
    <div className="gateway-decision"><span>Gateway</span><strong className={event?.decision === "DENY" ? "deny" : ""}>{event ? event.decision : "INITIAL STATE"}</strong><small>{event ? event.outcome : "No action applied"}</small></div>
    <Allocation name="Production" service={state.production} capacity={state.total_capacity} />
    <Allocation name="Staging" service={state.staging} capacity={state.total_capacity} />
    <div className="pool-summary"><span>Shared capacity</span><strong>{used} / {state.total_capacity}<small> connections allocated</small></strong></div>
    <div className="oracle-line">{event ? (event.safety.violations.join(" · ") || "Committed production allocation meets its protected minimum.") : "Both forks start from the same saved state."}</div>
    <div className="world-hashes"><span>state <code>{short(event?.after_hash || hash)}</code></span><span>version {state.version}</span></div>
  </section>;
}

function Workspace({ bundle, selected, select, recorded }: { bundle: Bundle; selected: number; select: (step: number) => void; recorded: number }) {
  const machine = bundle.counterfactual_replay;
  if (!machine) return <div className="notice error">This run did not reach counterfactual replay. Download its Evidence Bundle to inspect the failure.</div>;
  const step = Math.min(selected, bundle.original_proposals.length);
  const first = machine.first_divergence?.step;
  const verification = bundle.candidate.verification;
  const passedChecks = (prefix: string) => Object.entries(verification.checks).filter(([name]) => name.startsWith(prefix) && name.endsWith("-candidate"));
  const heldout = passedChecks("heldout-");
  const regression = passedChecks("regression-");
  const postRepair = bundle.safe_repair_after_promotion;
  return <>
    <div className="comparison-header"><div><span className="eyebrow">Safety Time Machine</span><h1>Same actions.<br />Different outcomes.</h1><p>One recorded trace, two independent SQLite worlds.<br />Inspect the committed effect of each policy decision.</p></div><div className="comparison-note"><span className="small-label">First decision divergence</span><strong>{first ? `Action ${first}` : "None found"}</strong><span>{first ? actionTitle(bundle.original_proposals[first - 1]) : "No verified divergence"}</span></div></div>
    <div className="experiment-grid">
      <aside className="trace panel"><div className="section-title"><span className="eyebrow">Recorded action trace</span><span className="count">{bundle.original_proposals.length} actions</span></div>
        <button className={`trace-step ${step === 0 ? "selected" : ""}`} onClick={() => select(0)} aria-pressed={step === 0}><span className="step-number">0</span><span><strong>Initial state</strong><small>Identical saved snapshot</small></span></button>
        {bundle.original_proposals.map((proposal, index) => <button key={index} className={`trace-step ${step === index + 1 ? "selected" : ""}`} onClick={() => select(index + 1)} aria-pressed={step === index + 1}><span className="step-number">{index + 1}</span><span><strong>{actionTitle(proposal)}</strong><small>{proposal.service}{first === index + 1 ? " · first divergence" : ""}</small></span></button>)}
        <div className="trace-caption">Handcrafted input.<br />Every state transition was executed by the sandbox through the Gateway.</div>
        <div className="recorded-count">{recorded} recorded events received</div>
        <div className="trace-navigation"><button disabled={step === 0} onClick={() => select(step - 1)}>Previous</button><button disabled={step === bundle.original_proposals.length} onClick={() => select(step + 1)}>Next</button></div>
      </aside>
      <div className="worlds"><World version="v0" fork={machine.forks.v0} step={step} hash={bundle.initial_state_hash} /><World version="v1" fork={machine.forks.v1} step={step} hash={bundle.initial_state_hash} /></div>
    </div>
    <div className="same-input"><span>Same initial state <code>{short(bundle.initial_state_hash)}</code></span><span>Same trace <code>{short(machine.source_trace_hash)}</code></span><span>Viewing {step === 0 ? "initial state" : `state after action ${step}`}</span></div>
    <div className="detail-grid">
      <section className="panel policy"><div className="section-title"><h2>Verified policy change</h2><span className="small-label">Handcrafted candidate</span></div>
        {bundle.candidate.patch.added_rules.map(rule => <div className="rule" key={rule.id}><span className="rule-effect">+ DENY</span><code>{rule.id}</code><div className="conditions">{rule.conditions.map((condition, index) => <div key={index}><span>{index === 0 ? "when" : "and"}</span><code>{condition.left}<br /><b>{symbols[condition.op] || condition.op}</b> {condition.right ?? String(condition.value)}</code></div>)}</div></div>)}
        <div className="policy-hash"><span>v0 <code>{short(machine.policy_hash_v0)}</code></span><span>→</span><span>v1 <code>{short(machine.policy_hash_v1)}</code></span></div>
        <p className="muted">The candidate only adds DENY conditions. Independent verification controls activation.</p>
      </section>
      <section className="panel verification"><div className="section-title"><h2>Independent verification</h2><Status pass={bundle.candidate.receipt.promoted}>{bundle.candidate.receipt.promoted ? "PROMOTED TO v1" : "NOT PROMOTED"}</Status></div>
        <dl className="verification-list"><div><dt>Original counterexample</dt><dd><Status pass={verification.checks.original_counterexample_blocked}>{verification.checks.original_counterexample_blocked ? "BLOCKED" : "FAIL"}</Status></dd></div><div><dt>Frozen regression checks</dt><dd>{regression.filter(([, passed]) => passed).length} / {regression.length}</dd></div><div><dt>Held-out checks</dt><dd>{heldout.filter(([, passed]) => passed).length} / {heldout.length}</dd></div><div><dt>DENY state preservation</dt><dd><Status pass={verification.checks.deny_preserves_state}>{verification.checks.deny_preserves_state ? "PASS" : "FAIL"}</Status></dd></div><div><dt>Overblocking negative control</dt><dd><Status pass={!bundle.negative_control.receipt.promoted && bundle.negative_control.verification?.status === "FAIL"}>{bundle.negative_control.receipt.promoted ? "FAIL" : "REJECTED"}</Status></dd></div></dl>
        <div className="repair-result"><div><span className="eyebrow">Separate legitimate workflow</span><h3>Safe staging repair after promotion</h3></div><Status pass={postRepair?.passed ?? false}>{postRepair?.passed ? "PASS" : "FAIL"}</Status>{postRepair && <p>Staging <strong>{postRepair.final_state.staging.allocated_connections}</strong> / {postRepair.final_state.staging.minimum_healthy_connections} minimum · production <strong>{postRepair.final_state.production.allocated_connections}</strong> / {postRepair.final_state.production.minimum_healthy_connections} minimum</p>}</div>
      </section>
    </div>
    <section className="panel deltas"><div className="section-title"><h2>Actual committed deltas</h2><span className="small-label">{step ? `Action ${step}` : "Initial state"}</span></div><div className="delta-columns">{(["v0", "v1"] as const).map(version => { const event = step ? machine.forks[version].events[step - 1] : null; return <div key={version}><h3>Policy {version}</h3>{event?.actual_delta.length ? <ul>{event.actual_delta.map((delta, index) => <li key={index}><code>{delta.resource} · {delta.field}</code><span>{delta.before} → <strong>{delta.after}</strong></span></li>)}</ul> : <p className="muted">{event?.decision === "DENY" ? "DENY: no committed state changes." : "No committed state changes."}</p>}</div>; })}</div></section>
    <div className="pending"><strong>Meta confirmation pending</strong><span>G3 discovery · G4 AI-generated repair · G7 adaptive re-attack</span><p>These fixture results do not establish autonomous discovery, model-generated repair, or live Meta performance.</p></div>
    <details className="provenance panel"><summary>Evidence provenance and integrity</summary><dl><div><dt>Run ID</dt><dd><code>{bundle.run_id}</code></dd></div><div><dt>Git commit</dt><dd><code>{bundle.project_git_commit}</code>{bundle.working_tree_dirty ? " · working tree had changes" : " · clean working tree"}</dd></div><div><dt>Initial state SHA-256</dt><dd><code>{bundle.initial_state_hash}</code></dd></div><div><dt>Bundle SHA-256</dt><dd><code>{bundle.bundle_hash}</code></dd></div><div><dt>Evidence kind</dt><dd><code>{bundle.evidence_kind}</code></dd></div><div><dt>Model calls</dt><dd>{String(bundle.real_model_call)}</dd></div></dl></details>
  </>;
}

function App() {
  const [bundle, setBundle] = useState<Bundle | null>(null);
  const [selected, select] = useState(0);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [recorded, setRecorded] = useState(0);
  useEffect(() => {
    let active = true;
    fetch("/api/evidence").then(async response => response.status === 404 ? null : readBundle(response)).then(data => { if (active) { setBundle(data); if (data) select(data.original_proposals.length); } }).catch(failure => { if (active) setError(String(failure.message || failure)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    setRecorded(0);
    if (!bundle) return;
    const stream = new EventSource(`/api/runs/${encodeURIComponent(bundle.run_id)}/events`);
    stream.addEventListener("gateway", event => {
      try { const item = JSON.parse((event as MessageEvent).data); if (item.execution_mode === "recorded_execution" && item.event) setRecorded(count => count + 1); } catch { stream.close(); }
    });
    stream.addEventListener("complete", () => stream.close());
    stream.onerror = () => stream.close();
    return () => stream.close();
  }, [bundle?.run_id]);
  async function run() {
    setBusy(true); setError("");
    try { const data = await readBundle(await fetch("/api/runs", { method: "POST" })); setBundle(data); select(data.original_proposals.length); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "The deterministic proof could not be completed."); }
    finally { setBusy(false); }
  }
  function download() {
    if (!bundle) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2) + "\n"], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = `${bundle.run_id}.json`; link.click(); URL.revokeObjectURL(url);
  }
  return <><header className="site-header"><a className="brand" href="/" aria-label="SENTINEL Evidence Workspace">SENTINEL<span>Counterexample-Guided Safety for AI Agents</span></a><div className="header-actions"><button className="secondary" onClick={download} disabled={!bundle || busy}>Download JSON</button><button className="primary" onClick={run} disabled={busy}>{busy ? "Executing SQLite proof…" : "Run deterministic proof"}</button></div></header><main><div className="fixture-banner"><span className="fixture-dot" /><strong>Handcrafted fixture</strong><code>deterministic_test_fixture</code><span>No model calls · recorded deterministic execution</span></div>{error && <div role="alert" className="notice error">{error}</div>}{bundle && bundle.status !== "PASS" && <div role="alert" className="notice error">Deterministic proof failed. These are partial results; inspect the Evidence Bundle before drawing conclusions.</div>}<div className="activity" role="status" aria-live="polite">{busy ? "Executing isolated SQLite transitions, independent verification, promotion, and counterfactual replay. No inference provider is called." : loading ? "Loading saved evidence…" : ""}</div>{bundle ? <Workspace bundle={bundle} selected={selected} select={select} recorded={recorded} /> : !loading && <div className="empty panel"><span className="eyebrow">Evidence workspace</span><h1>Start with a reproducible proof.</h1><p>Run the deterministic fixture to verify policy rejection, controlled promotion, and two real SQLite futures from the same initial state.</p><button className="primary" onClick={run} disabled={busy}>Run deterministic proof</button><p className="muted">Meta-dependent discovery, repair, and adaptive re-attack remain pending.</p></div>}</main><footer><span>SENTINEL-CG 2.0</span><span>Evidence first. Policy activation follows independent verification.</span></footer></>;
}

createRoot(document.getElementById("root")!).render(<App />);
