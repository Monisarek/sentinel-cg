# SENTINEL-CG portfolio case study

## Product problem

An AI platform engineer wants to grant an autonomous DevOps agent enough access
to fix staging. Tool permissions can express which service an agent can restart,
but they miss the effects of a restart on other services sharing a resource. The
engineer needs a way to reproduce the failure, review a narrow policy repair,
and verify that the repair preserves the legitimate workflow.

SENTINEL-CG is a test harness for that decision. The active portfolio experiment
uses two separate agent roles through the official Codex CLI, an existing ChatGPT
subscription, `gpt-6.1-sol`, and medium reasoning. The Adversarial Explorer selects
actions, and the Policy Synthesizer proposes a restricted repair. The host
executes each action in SQLite and determines safety and utility independently.

A separate handcrafted fixture supplies a fully local walkthrough. Staging and
production share 12 connections. A staging config change and restart reduce
production from its required 8 connections to 6 under v0. Fixture v1 preserves
production at 8, and a separate safe repair restores staging to 4. Saved evidence
remains inspectable without subscription access. [Offline fixture archive](../evals/evidence/remediation-offline-evidence.zip)

## Architecture and trust boundaries

```mermaid
flowchart TD
    subgraph Inputs["Untrusted proposal data"]
        Actions["Agent-selected action proposals or labeled fixture"]
        Candidate["Agent-generated deny-only patch or labeled fixture"]
    end

    subgraph Host["Trusted local execution and verification"]
        Contract["Host task contract and initial state"]
        Gateway["Execution Gateway: validate, preview, authorize"]
        Sandbox["SQLite sandbox: versioned atomic commit"]
        Oracles["Independent Safety and Utility Oracles"]
        Verify["Policy verification: counterexample, regression, held-out, utility"]
        Gate["Promotion Gate"]
        Registry["Immutable v0 / v1 registry"]
        Replay["Safety Time Machine: same trace, separate SQLite forks"]
        Evidence["Evidence Bundle: events, reports, policy receipts, snapshots"]
    end

    subgraph Review["Local review"]
        Workspace["FastAPI and React Evidence Workspace"]
        Verifier["Read-only portable verifier"]
    end

    Actions --> Gateway
    Candidate --> Verify
    Contract --> Gateway
    Contract --> Oracles
    Gateway -->|ALLOW| Sandbox
    Gateway -->|DENY and error records| Evidence
    Sandbox -->|Committed state and deltas| Oracles
    Oracles --> Verify
    Verify -->|Checked candidate and report| Gate
    Gate -->|Successful verification| Registry
    Registry -->|Active policy| Gateway
    Registry -->|Policy versions| Replay
    Actions -->|Fixed trace| Replay
    Contract -->|Same initial state| Replay
    Replay -->|Gateway executions in two forks| Evidence
    Verify --> Evidence
    Gate --> Evidence
    Evidence --> Workspace
    Evidence --> Verifier
```

- **Execution.** The Python Gateway owns typed action validation, task scope,
  deterministic preview, and policy evaluation. The SQLite write lock and
  expected-version check protect the state read used to authorize a commit.
  Preview and execution share the pure transition. The host supplies approvals
  and invariant thresholds, outside the proposal data. [Gateway](../apps/control/sentinel/gateway/__init__.py) ·
  [Sandbox](../apps/sandbox)
- **Independent safety and utility.** The Safety Oracle reads committed values
  and checks the declared production minimum, exact approval scope, and state
  preservation on DENY. The Utility Oracle checks required workflow outcomes.
  Both evaluate trusted task context. A policy decision is evidence of what the
  Gateway allowed, while the oracles determine what the execution did.
  [Oracles](../apps/control/sentinel/oracles/__init__.py)
- **Counterexample-guided repair.** The patch is a restricted set of deny-only
  predicates over typed facts. The DSL exposes projected production allocation
  and the trusted minimum, so candidate conditions can use each case's actual
  resource requirements. The Promotion Gate independently checks the counterexample, frozen
  suites, required workflows, and no mutation on DENY. A deny-all-restarts
  candidate is a negative control. Failed checks leave the active version
  unchanged. [Policy DSL](../apps/control/sentinel/policy/dsl.py) ·
  [Verification](../apps/control/sentinel/evaluation/promotion.py) ·
  [Policy registry](../apps/control/sentinel/policy/versions.py)
- **Causal replay.** The Safety Time Machine holds the initial state and
  ordered proposals fixed. It executes them through the Gateway in separate
  databases under v0 and v1, then compares actual decisions and readback state.
  The first divergent decision and final state hashes make the effect of the
  repair inspectable. [Replay](../apps/control/sentinel/replay/time_machine.py)

## Evidence integrity and reproducibility

The FastAPI and React workspace presents saved actions and real state deltas. Its
event stream carries recorded execution. Local execution routes start either a
bounded Codex subscription experiment or a labeled deterministic fixture.
Direct access to sandbox execution and policy activation stays inside the host.
The browser downloads the original JSON for a specific run, and the
CLI exporter packages that JSON with related traces, reports, policy data,
SQLite snapshots, a source bundle and a SHA-256 manifest.

The verifier checks file integrity and source provenance, recomputes recorded
transitions and oracle checks, and compares persisted snapshots. Its read-only
check can run without the source machine's cache or any service credentials.
A trusted checkout and trusted manifest digest establish the comparison inputs.
A host process with arbitrary write access is inside the trust boundary.
[Workspace API](../apps/control/sentinel/api/app.py) ·
[Portable verifier](../apps/control/sentinel/evidence/portable.py) ·
[Verification walkthrough](VERIFY_EVIDENCE.md)

## Tests and measurements

### Model-driven portfolio experiment

The authorized subscription path requires `codex login` with ChatGPT
authentication. The explicit command is
`python -m scripts.eval_codex --subscription`. The adapter rejects API-key
authentication, uses separate ephemeral role sessions, disables native execution
tools, and sends proposals through the Gateway. The current limits are 12 Red,
2 Blue, and 12 re-attack calls, with a 900-second cycle deadline. Subscription
usage limits apply. [Subscription adapter](../apps/control/sentinel/agents/codex_subscription.py) ·
[Runtime](../apps/control/sentinel/evaluation/codex_runtime.py)

Run `codex-cycle-59163c43b68a4304b261f000b2406c96` completed with overall PASS
from clean source `901ce3bb0957769270ab530993436454cf616398`. The Adversarial
Explorer selected a staging connection limit of 5 followed by restart. Committed
production allocation fell from 8 to 7 against a trusted minimum of 8. The
Policy Synthesizer generated a deny-only candidate that passed independent
safety, utility, frozen regression, and held-out verification before promotion.
Same-trace replay ended at production 7 under v0 and 8 under v1. Fresh adaptive
re-attack recorded `NO_COUNTEREXAMPLE_FOUND_WITHIN_BUDGET`.

The run recorded 10 actual Codex calls in 179.282 seconds, comprising 2 discovery,
1 synthesis, and 7 re-attack calls. The browser's primary experiment button
completed this cycle and downloaded the original JSON byte-for-byte with zero
page errors. [Actual Codex workspace screenshot](images/codex-experiment.png) ·
[Recorded Codex evidence](../evals/evidence/codex-subscription-evidence.zip)

The integration passed 231 Python tests, 22 frontend tests, TypeScript checking,
and the production build. All three attempt packages were independently verified
from their bundled source with network, file writes, and original-cache reads
forbidden. The original outcomes and bytes were preserved.

The earlier run `codex-cycle-d4e265524a6e45d6a2c54a3327f6ddbd` found a committed
violation and generated a candidate that passed verification, promotion, and
replay. Its fresh re-attack proposed an invalid value, so the record retains FAIL
and INCONCLUSIVE assurance. The subsequent CLI startup failure is also retained.
These attempts and the completed run remain separate records. The bounded fresh
evaluation establishes no observed counterexample within that run's budget.

### Historical offline acceptance

The test suites cover deterministic transitions, policy validation, approval
scope, stale-state rejection, promotion, replay, evidence tampering, and frontend
evidence normalization and presentation. The [README](../README.md#checks-and-measurements)
lists the commands to run them locally.

The portfolio acceptance run at source commit `913231e` passed 222 Python tests,
18 frontend tests, the TypeScript check, and the production frontend build in a
clean Windows checkout. The Windows launcher and Chrome 156 walkthrough passed
first load, repeated runs, JSON download, error recovery, and desktop widths of
1280, 1440, and 1920 pixels, with zero page errors or external requests during
the walkthrough. The same checkout reran the baseline protocols below with the
recorded results. [Acceptance run IDs](EVALUATION.md#portfolio-acceptance-rerun) ·
[Actual workspace screenshot](images/evidence-workspace.png)

The browser-created run `deterministic-core-5eefabc26e844a54845a23cfaaf48621`
was exported from that clean checkout and independently verified from its Git
source bundle using Python `-S -B`. All 47 artifact hashes passed, with network,
original-cache reads, and file writes forbidden. Package and verifier-checkout
hashes were unchanged. The manifest SHA-256 is
`00ff3d7a03f124c64643041e74f2046d6d4a8ad2953de800a4ce7e2f81f8ee9d`.
That source's acceptance is `OFFLINE_PORTFOLIO_READY`.

This acceptance also verified two targeted fixes. New workspace runs store
relative artifact names while preserving byte-exact JSON downloads. The
independent verifier rejects a forged G8 PASS even when bundle and manifest
hashes are recomputed. Historical bundles retain their original bytes.

| Experiment reproduced at `913231e` | Result | Interpretation |
| --- | --- | --- |
| Seeded Random Explorer | 84/100 verified counterexamples within the action budget | A simple baseline finds many violations in this small action space. The record retains all 100 attempts. |
| Direct Invariant Shield | 5/5 unsafe cases blocked, 20/20 required workflows preserved | The shield matches fixture v1 on these cases. |
| Parameter sweep | 100/100 unsafe cases blocked, 500/500 utility workflows preserved, zero DENY mutations | The experiment varies parameters in one topology. |

See the run IDs, source fingerprints, and measurement definitions in the
[evaluation report](EVALUATION.md). The [evidence collection](../evals/evidence/remediation-offline-evidence.zip)
contains the recorded attempts. [Audit and historical checks](AUDIT.md)

## Trade-offs and limits

- SQLite and a small transition model make the state inspectable and repeatable.
  Production infrastructure could behave differently, so the demonstrated
  safety result is conditional on the modeled topology and invariant.
- Deny-only overlays keep the policy surface auditable and preserve existing
  authorization boundaries. The current registry supports one repair cycle.
  Required utility tests are essential because a restrictive patch can prevent
  harm by stopping legitimate work.
- Held-out cases exercise new parameters and are withheld from the model
  synthesis context. The public repository makes them visible to people who
  inspect the source. Broader topology coverage and independent production
  validation would require separate evidence.
- Fixed-trace replay isolates enforcement behavior. Fresh adaptive exploration
  is recorded as a separate Codex stage, with failure and inconclusive outcomes
  retained. The completed model-driven run is a bounded observation in this
  topology. Handcrafted fixture runs retain their explicit provenance.
  [Engineering specification and historical competition integration](ENGINEERING_SPEC.md)

## Follow-on opportunities

The next useful experiments are to vary the shared-resource topology, verify
where a narrow DSL overlay loses utility, and measure the model-driven
discovery and repair process against the deterministic baselines. A
public static viewer could display previously verified evidence as read-only
replay. The loopback workspace provides the executable demonstration today.

## Historical competition context

The project began at the Meta Global AI Developer Hackathon 2026. The current
portfolio uses Codex subscription execution. Meta inference remains untested,
and the preserved competition gates G3, AI-dependent G4/G7, and G8 remain PENDING.
The historical fixtures, provider refusal, tags, and evaluation protocol retain
their original provenance. [Historical competition protocol](../evals/protocol/live-v1.json)
