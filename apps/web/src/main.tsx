import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { downloadUrl, normalizeEvidence, sourceLabel } from "./evidence";
import type { Bundle, Fork, Proposal, Service } from "./evidence";
import "./style.css";

const short = (value: string) => value.slice(0, 12);
const actionTitle = (action: Proposal) => action.name === "set_service_config"
  ? `Set ${action.environment} ${action.key} = ${action.value}`
  : `${action.name.replaceAll("_", " ")} · ${action.environment}`;
const symbols: Record<string, string> = { eq: "=", neq: "≠", lt_field: "<", lte_field: "≤", gt_field: ">", gte_field: "≥", eq_field: "=", neq_field: "≠" };

async function readBundle(response: Response): Promise<Bundle> {
  if (!response.ok) throw new Error(`Evidence request failed (HTTP ${response.status}).`);
  return normalizeEvidence(await response.json());
}

function Status({ pass, children }: { pass: boolean; children: ReactNode }) {
  return <span className={`status ${pass ? "pass" : "fail"}`}>{children}</span>;
}

function RecordedStatus({ value }: { value: string }) {
  return <span className={`status ${value === "PASS" ? "pass" : ["FAIL", "BLOCKED", "STOPPED"].includes(value) ? "fail" : "unconfirmed"}`}>{value}</span>;
}

function CheckStatus({ value, success = "PASS" }: { value: boolean | undefined; success?: string }) {
  return value === undefined ? <RecordedStatus value="PENDING" /> : <Status pass={value}>{value ? success : "FAIL"}</Status>;
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

function TimeMachine({ bundle, selected, select, recorded }: { bundle: Bundle; selected: number; select: (step: number) => void; recorded: number }) {
  const machine = bundle.counterfactual_replay;
  if (!machine) return <div className="notice">{bundle.replay_notice}</div>;
  const step = Math.min(selected, bundle.original_proposals.length);
  const first = machine.first_divergence?.step;
  return <>
    <div className="comparison-header"><div><span className="eyebrow">Safety Time Machine</span><h1>Same actions.<br />Different outcomes.</h1><p>One recorded trace, two independent SQLite worlds.<br />Inspect the committed effect of each policy decision.</p></div><div className="comparison-note"><span className="small-label">First decision divergence</span><strong>{first ? `Action ${first}` : "None found"}</strong><span>{first ? actionTitle(bundle.original_proposals[first - 1]) : "No verified divergence"}</span></div></div>
    <div className="experiment-grid">
      <aside className="trace panel"><div className="section-title"><span className="eyebrow">Recorded action trace</span><span className="count">{bundle.original_proposals.length} actions</span></div>
        <button className={`trace-step ${step === 0 ? "selected" : ""}`} onClick={() => select(0)} aria-pressed={step === 0}><span className="step-number">0</span><span><strong>Initial state</strong><small>Identical saved snapshot</small></span></button>
        {bundle.original_proposals.map((proposal, index) => <button key={index} className={`trace-step ${step === index + 1 ? "selected" : ""}`} onClick={() => select(index + 1)} aria-pressed={step === index + 1}><span className="step-number">{index + 1}</span><span><strong>{actionTitle(proposal)}</strong><small>{proposal.service}{first === index + 1 ? " · first divergence" : ""}</small></span></button>)}
        <div className="trace-caption">{bundle.fixture ? "Deterministic fixture input." : "Recorded source action trace."}<br />Every displayed state transition was executed by the sandbox through the Gateway.</div>
        <div className="recorded-count">{recorded} recorded events received</div>
        <div className="trace-navigation"><button disabled={step === 0} onClick={() => select(step - 1)}>Previous</button><button disabled={step === bundle.original_proposals.length} onClick={() => select(step + 1)}>Next</button></div>
      </aside>
      <div className="worlds"><World version="v0" fork={machine.forks.v0} step={step} hash={bundle.initial_state_hash} /><World version="v1" fork={machine.forks.v1} step={step} hash={bundle.initial_state_hash} /></div>
    </div>
    <div className="same-input"><span>Same initial state <code>{short(bundle.initial_state_hash)}</code></span><span>Same trace <code>{short(machine.source_trace_hash)}</code></span><span>Viewing {step === 0 ? "initial state" : `state after action ${step}`}</span></div>
    <section className="panel deltas"><div className="section-title"><h2>Actual committed deltas</h2><span className="small-label">{step ? `Action ${step}` : "Initial state"}</span></div><div className="delta-columns">{(["v0", "v1"] as const).map(version => { const event = step ? machine.forks[version].events[step - 1] : null; return <div key={version}><h3>Policy {version}</h3>{event?.actual_delta.length ? <ul>{event.actual_delta.map((delta, index) => <li key={index}><code>{delta.resource} · {delta.field}</code><span>{delta.before} → <strong>{delta.after}</strong></span></li>)}</ul> : <p className="muted">{event?.decision === "DENY" ? "DENY: no committed state changes." : "No committed state changes."}</p>}</div>; })}</div></section>
  </>;
}

function PolicyVerification({ bundle }: { bundle: Bundle }) {
  const verification = bundle.candidate.verification;
  const receipt = bundle.candidate.receipt;
  const machine = bundle.counterfactual_replay;
  const passedChecks = (prefix: string) => Object.entries(verification?.checks || {}).filter(([name]) => name.startsWith(prefix) && name.endsWith("-candidate"));
  const heldout = passedChecks("heldout-");
  const regression = passedChecks("regression-");
  const postRepair = bundle.safe_repair_after_promotion;
  const utility = postRepair || bundle.verified_utility;
  const negative = bundle.negative_control;
  return <>
    <div className="detail-grid">
      <section className="panel policy"><div className="section-title"><h2>{receipt?.promoted ? "Verified policy change" : "Candidate policy change"}</h2><span className="small-label">{bundle.candidate_origin}</span></div>
        {bundle.candidate.patch ? bundle.candidate.patch.added_rules.map(rule => <div className="rule" key={rule.id}><span className="rule-effect">+ {rule.effect.toUpperCase()}</span><code>{rule.id}</code><div className="conditions">{rule.conditions.map((condition, index) => <div key={index}><span>{index === 0 ? "when" : "and"}</span><code>{condition.left}<br /><b>{symbols[condition.op] || condition.op}</b> {condition.right ?? String(condition.value)}</code></div>)}</div></div>) : <p className="muted">No candidate patch has been recorded.</p>}
        {machine && <div className="policy-hash"><span>v0 <code>{short(machine.policy_hash_v0)}</code></span><span>→</span><span>v1 <code>{short(machine.policy_hash_v1)}</code></span></div>}
        <p className="muted">The candidate only adds DENY conditions. Independent verification controls activation.</p>
      </section>
      <section className="panel verification"><div className="section-title"><h2>Independent verification</h2>{receipt ? <Status pass={receipt.promoted}>{receipt.promoted ? `PROMOTED TO ${receipt.active_version}` : "NOT PROMOTED"}</Status> : <RecordedStatus value="PENDING" />}</div>
        {receipt && <p className="muted"><code>{receipt.reason}</code></p>}
        <dl className="verification-list"><div><dt>Original counterexample</dt><dd><CheckStatus value={verification?.checks.original_counterexample_blocked} success="BLOCKED" /></dd></div><div><dt>Frozen regression checks</dt><dd>{regression.length ? `${regression.filter(([, passed]) => passed).length} / ${regression.length}` : "Not recorded"}</dd></div><div><dt>Held-out checks</dt><dd>{heldout.length ? `${heldout.filter(([, passed]) => passed).length} / ${heldout.length}` : "Not recorded"}</dd></div><div><dt>DENY state preservation</dt><dd><CheckStatus value={verification?.checks.deny_preserves_state} /></dd></div><div><dt>Overblocking negative control</dt><dd>{negative ? <CheckStatus value={!negative.receipt.promoted && negative.verification?.status === "FAIL"} success="REJECTED" /> : "Not recorded"}</dd></div></dl>
        {verification && <details><summary>All independent checks · {verification.status}</summary><dl className="verification-list">{Object.entries(verification.checks).map(([name, value]) => <div key={name}><dt><code>{name}</code></dt><dd><CheckStatus value={value} /></dd></div>)}</dl></details>}
        <div className="repair-result"><div><span className="eyebrow">Separate legitimate workflow</span><h3>{postRepair ? "Safe staging repair after promotion" : "Verified legitimate staging workflow"}</h3></div><CheckStatus value={utility?.passed} />{utility ? <p>Staging <strong>{utility.final_state.staging.allocated_connections}</strong> / {utility.final_state.staging.minimum_healthy_connections} minimum · production <strong>{utility.final_state.production.allocated_connections}</strong> / {utility.final_state.production.minimum_healthy_connections} minimum{!postRepair && <><br />Source: independent verification · <code>{bundle.verified_utility?.source}</code></>}</p> : <p>No saved legitimate-workflow result is available.</p>}</div>
      </section>
    </div>
  </>;
}

function RuntimeStages({ bundle }: { bundle: Bundle }) {
  if (!bundle.stages.length) return null;
  return <section className="panel deltas"><div className="section-title"><h2>Recorded runtime stages</h2><RecordedStatus value={bundle.runtime_status} /></div>
    {bundle.stages.map(stage => <details className="runtime-stage" key={stage.key} open={stage.status === "STOPPED" || stage.status === "IN PROGRESS"}>
      <summary>{stage.title} <RecordedStatus value={stage.status} /> <span className="small-label">{stage.calls.length} recorded calls · {stage.events.length} Gateway events</span></summary>
      {stage.result && <p>{stage.result}</p>}{stage.error && <p role="alert">{stage.error}</p>}
      {stage.calls.map((call, index) => <div className="runtime-call" key={index}><span className="eyebrow">Call {index + 1} · {call.provider}</span><p>Actual model ID: <code>{call.model_id}</code> · real model call: <code>{String(call.real_model_call)}</code></p><p className="muted">Provenance: <code>{call.evaluation_status}</code> · response <code>{call.response_id || "Not recorded"}</code> · prompt <code>{call.prompt_hash || "Not recorded"}</code></p><p className="muted">Reported usage: <code>{JSON.stringify(call.usage)}</code></p></div>)}
      {stage.decisions.length > 0 && <><h3>Agent Proposed Actions</h3>{stage.decisions.map((decision, index) => <pre key={index}>{JSON.stringify(decision, null, 2)}</pre>)}</>}
      {stage.events.length > 0 && <><h3>Actual Gateway tool calls</h3>{stage.events.map((event, index) => <details key={index}><summary>Action {index + 1} · {String(event.decision)} · {String(event.outcome)}</summary><pre>{JSON.stringify(event, null, 2)}</pre></details>)}</>}
      {Object.keys(stage.oracle).length > 0 && <><h3>Committed-state Oracle results</h3><pre>{JSON.stringify(stage.oracle, null, 2)}</pre></>}
      {stage.raw ? <details><summary>Complete recorded stage data</summary><pre>{JSON.stringify(stage.raw, null, 2)}</pre></details> : <p className="muted">This stage has not been recorded.</p>}
    </details>)}
  </section>;
}

export function Workspace({ bundle, selected, select, recorded }: { bundle: Bundle; selected: number; select: (step: number) => void; recorded: number }) {
  return <>
    {bundle.errors.length > 0 && <div role="alert" className="notice error">{bundle.errors.map((error, index) => <div key={index}>{error}</div>)}</div>}
    <TimeMachine bundle={bundle} selected={selected} select={select} recorded={recorded} />
    <PolicyVerification bundle={bundle} />
    <RuntimeStages bundle={bundle} />
    <div className="pending"><strong>Meta acceptance evidence</strong><span>G3 discovery <RecordedStatus value={bundle.gates.G3} /> · G4 synthesis <RecordedStatus value={bundle.gates.G4_AI} /> · G7 adaptive re-attack <RecordedStatus value={bundle.gates.G7_AI} /></span><p>{bundle.fixture ? "Deterministic test fixtures do not establish autonomous discovery, model-generated repair, or live Meta performance." : "Gate status requires genuine Meta call provenance and the corresponding recorded runtime checks. PENDING does not establish a completed gate."}</p></div>
    <details className="provenance panel"><summary>Evidence provenance and integrity</summary><dl><div><dt>Run ID</dt><dd><code>{bundle.run_id}</code></dd></div><div><dt>Git commit</dt><dd><code>{bundle.project_git_commit}</code>{bundle.working_tree_dirty ? " · working tree had changes" : " · clean working tree"}</dd></div><div><dt>Initial state SHA-256</dt><dd><code>{bundle.initial_state_hash}</code></dd></div><div><dt>Bundle SHA-256</dt><dd><code>{bundle.bundle_hash}</code></dd></div><div><dt>Evidence kind</dt><dd><code>{bundle.evidence_kind}</code></dd></div><div><dt>Provider</dt><dd>{bundle.provider}</dd></div><div><dt>Real model call</dt><dd>{String(bundle.real_model_call)}</dd></div><div><dt>Actual model IDs</dt><dd>{[...new Set(bundle.model_calls.map(call => call.model_id))].join(" · ") || "No recorded model calls"}</dd></div></dl></details>
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
    const runId = new URLSearchParams(window.location.search).get("run_id");
    const endpoint = runId ? `/api/runs/${encodeURIComponent(runId)}` : "/api/evidence";
    fetch(endpoint).then(async response => response.status === 404 && !runId ? null : readBundle(response)).then(data => { if (active) { setBundle(data); if (data) select(data.original_proposals.length); } }).catch(failure => { if (active) setError(String(failure.message || failure)); }).finally(() => { if (active) setLoading(false); });
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
    const link = document.createElement("a"); link.href = downloadUrl(bundle); link.download = `${bundle.run_id}.json`; link.click();
  }
  return <><header className="site-header"><a className="brand" href="/" aria-label="SENTINEL Evidence Workspace">SENTINEL<span>Counterexample-Guided Safety for AI Agents</span></a><div className="header-actions"><button className="secondary" onClick={download} disabled={!bundle || busy}>Download JSON</button><button className="primary" onClick={run} disabled={busy}>{busy ? "Executing SQLite proof…" : "Run deterministic proof"}</button></div></header><main><div className="fixture-banner"><span className="fixture-dot" /><strong>{bundle ? sourceLabel(bundle) : "Deterministic proof"}</strong><code>{bundle?.evidence_kind || "deterministic_test_fixture"}</code><span>{bundle ? `${bundle.real_model_call ? "Model calls recorded" : "No real model calls"} · ${bundle.runtime_status}` : "No inference provider is called"}</span></div>{error && <div role="alert" className="notice error">{error}</div>}{bundle && bundle.status !== "PASS" && <div role="alert" className="notice error">Run status: {bundle.status} · {bundle.runtime_status}. These recorded results may be partial; completed stages remain inspectable below.</div>}<div className="activity" role="status" aria-live="polite">{busy ? "Executing isolated SQLite transitions, independent verification, promotion, and counterfactual replay. No inference provider is called." : loading ? "Loading saved evidence…" : ""}</div>{bundle ? <Workspace bundle={bundle} selected={selected} select={select} recorded={recorded} /> : !loading && <div className="empty panel"><span className="eyebrow">Evidence workspace</span><h1>Start with a reproducible proof.</h1><p>Run the deterministic fixture to verify policy rejection, controlled promotion, and two real SQLite futures from the same initial state.</p><button className="primary" onClick={run} disabled={busy}>Run deterministic proof</button><p className="muted">Meta-dependent discovery, repair, and adaptive re-attack remain pending.</p></div>}</main><footer><span>SENTINEL-CG 2.0</span><span>Evidence first. Policy activation follows independent verification.</span></footer></>;
}

if (typeof document !== "undefined") {
  const root = document.getElementById("root");
  if (root) createRoot(root).render(<App />);
}
