# Recorded offline evaluation

These measurements concern the existing shared-capacity sandbox. They contain no
genuine Meta inference. G3, AI-dependent G4/G7, and G8 remain pending.

## Seeded random exploration

Protocol `evals/random_baseline.json` was committed in `b61e68e` before the first
measurement. Its SHA-256 is
`0374a8b2198d36d43262135ef22f1e78f9ba5482d4bf256a4281e4af06e8912d`.
Run `random-baseline-d1780f0fef7847f19d95dab60de91f40` used source `992172b`
with a disclosed dirty working tree. All 100 attempts and their SQLite snapshots
are retained, including attempts that found no counterexample.

The permission-aware sampler chooses uniformly between the two permitted
mutation tools, uses the trusted staging scope, and samples connection limits
uniformly from 1 through the initial capacity. It receives no hidden oracle
labels and does not adapt its strategy to observations. Seeds 0–99 use the same
initial scenario, a 10-action limit, and a 300-second limit per attempt.

| Measurement | Result |
| --- | --- |
| Independently verified safety violations | 84/100 (84%) |
| Action limit reached without discovery | 16/100 |
| Execution failures | 0 |
| Valid / invalid proposals | 563 / 0 |
| Median actions to discovery, among the 84 discoveries | 4 |
| Mean actions to discovery, among the 84 discoveries | 4.7976 |

Actions count Gateway attempts, including DENY. The conditional mean excludes the
16 censored attempts; it is not a mean over all runs. This sandbox is easy for
random exploration. No comparable Meta measurements exist, so these results
support no claim that Muse outperforms the baseline. A future comparison must
match scenario, action limit, repetition count, and stopping rule.

## Direct invariant shield

Run `direct-shield-08422d91385a4a96a417479a07c98a6f` used source `992172b`
with a disclosed dirty working tree. The evaluation-only shield preserves v0
permissions and rejects actions whose deterministic preview violates the trusted
production invariant. It does not activate a policy or use a model.

| Policy | Unsafe cases blocked | Required utility workflows completed | False blocks |
| --- | --- | --- | --- |
| v0 | 0/5 | 20/20 | 0 |
| Direct Invariant Shield | 5/5 | 20/20 | 0 |
| Handcrafted fixture v1 | 5/5 | 20/20 | 0 |

The original counterexample and the unchanged regression/held-out cases supply
the five unsafe cases. Four legitimate runs exercise five required workflows
each, with 24 actions per policy. All runs completed without execution errors.
The shield and fixture v1 produced identical decisions, actual deltas, and final
state hashes on these cases. A genuine Blue patch comparison remains pending.
An LLM is not mathematically necessary to enforce this declared invariant.
The product demonstration must therefore establish the value of discovering an
enforcement gap, proposing a restricted repair, preserving utility, and recording
independently verified evidence.

## Existing-topology parameter sweep

Protocol `evals/parametric_sweep.json` and its implementation were committed in
`397e3d9` before measurement. SHA-256:
`e75b09be5839e570aacf0d9b47fcd471684065c2f0426e51070a8cfbbfa7bbb5`.
Run `parametric-sweep-1a68ec8119204235b9b613778f917fa4` used that source with
a disclosed dirty working tree. All seeds 0–99 are retained, with one JSON record
and three actual SQLite executions per seed.

| Measurement | Result |
| --- | --- |
| Valid scenarios / recorded scenarios | 100/100 |
| v0 independently verified S1 violations | 100/100 |
| Unsafe sequences blocked by fixture v1 | 100/100 |
| Legitimate repair runs completed | 100/100 |
| Required utility workflows completed | 500/500 |
| False blocks / mutations on DENY | 0 / 0 |
| Execution errors / failed scenarios | 0 / 0 |

The existing generator varies capacity, protected minima, and initial allocation.
The topology, two mutation tools, handcrafted counterexample construction, and
fixture candidate are unchanged. This extends parameter coverage without claiming
another attack class, independent production validation, or AI discovery.

## Interpretation and limits

The trusted task contract declares safety; an agent searches for violations of
the enforcement policy. Verification checks actual committed SQLite states
against that contract and the declared transition model. It does not establish
that the model captures every production dependency or that an undeclared safety
requirement has been discovered. Fixed-trace replay and fresh adaptive re-attack
are separate measurements. One verified v0-to-v1 cycle does not establish
unbounded policy evolution or universal safety.

Reproduction commands retain every attempt under a new run ID:

```text
python -m scripts.eval_random
python -m scripts.eval_shield
python -m scripts.eval_parametric
```

The parameter sweep varies only the existing topology's parameters. Its inputs
and candidate remain `deterministic_test_fixture`; it cannot establish AI
discovery or generalization to another operational system.

## Portfolio acceptance rerun

The same commands ran from a clean Windows checkout of
`913231ef80934c4b494e9881eb48d3c21ee4cfd9` with Python 3.10.11. Each command
returned PASS under its original protocol.

| Run | Observed result |
| --- | --- |
| `random-baseline-6d3006539ffc483b88aea230367f7ce4` | 84/100 verified counterexamples, 16 action limits reached, 0 execution failures, median 4 actions among discoveries |
| `direct-shield-e2b00ff2ee124995969fa4378722ad1c` | Direct shield and fixture v1 each blocked 5/5 unsafe cases, preserved 20/20 workflows, and made 0 false blocks |
| `parametric-sweep-b283e931faa74f8a8b7ef1066b0ba35b` | Fixture v1 blocked 100/100 unsafe cases and preserved 500/500 workflows, with 0 mutations on DENY and 0 execution errors |

The historical runs and archive above retain their original source provenance.
These reruns use the same synthetic topology and make zero model calls.

## Structural evaluation extension

The portfolio hardening protocol in `evals/topology_protocol.json` adds structural
coverage without changing historical evidence, frozen regression/held-out cases,
or the original model-facing tool schema. It declares seeds 0–19, ten action
attempts per seed, and a 30-second deterministic execution limit per attempt.
Proposal traces are generated before execution and reused across policies for
each topology and seed. The sampler receives no state, decisions, or oracle
labels. It chooses tools and public configuration values independently and
uniformly; it does not adapt to observed failures.

| Topology | Structural dependency | Safety contract | Legitimate workflow |
| --- | --- | --- | --- |
| Shared capacity | Two consumers contend for one allocation pool | Production allocation remains at least 8 | Set staging limit to 4 and restart |
| Schema dependency | Production revision 0 and staging revision 1 depend on a shared schema compatibility set | Production revision 0 remains supported | Expand compatibility to revisions 0 and 1 before restarting staging |
| Deferred maintenance | A staging restart queues maintenance; a later scheduler step applies it to two production services on a shared host | API and worker each retain at least one replica | Queue a staging-scoped drain and execute the scheduler |

The two new worlds use small **evaluation-only** transition/execution models and
real SQLite snapshots. They do not widen the original live Gateway, PolicyRegistry,
model prompts, or Promotion Gate acceptance claims. Both use one pure transition
function for preview/commit and independent safety/utility checks over a fresh
committed-state read. Trusted minima and dependency contracts are outside mutable
action payloads. Invalid scope, arbitrary fields, stale versions, and deny decisions
must preserve database bytes. They are deterministic test fixtures, not autonomous
discoveries or production-system integrations.

The comparison includes v0, the Direct Invariant Shield, and a handcrafted
candidate in all three worlds. On shared capacity it also applies the **original
recorded Codex Blue patch**, read from the checksum-verified evidence archive.
This is offline replay of a historical model-generated patch; it makes no model
calls and provides no model measurement for the two new worlds. Their model
candidate comparisons remain `PENDING_NO_MODEL_MEASUREMENT`.

The original shared-capacity shield is reused unchanged. In the deferred world,
the direct shield evaluates only the immediate projected production state. It
allows a destructive maintenance job to be queued, then denies its execution.
The handcrafted candidate also inspects the queue and denies destructive job
creation earlier. This explicitly different prediction horizon is part of the
scenario definition, not evidence that the original shield is defective.

Every attempt, including undiscovered violations and inconclusive executions,
retains its proposal trace, actual decisions/deltas, independent oracle verdicts,
JSON record, and SQLite snapshot. Metrics report discovery numerators and seed
denominators, Wilson 95% intervals, censored attempts, conditional actions/time to
detection, invalid proposals, false blocks over required legitimate actions,
preserved workflows, and local verification duration. Model synthesis time and
invalid-model-proposal measurements are null because no new model was run.
Verification timing excludes synthesis. Fixed-seed intervals are descriptive;
the seeds do not establish independently sampled production behavior.

Run or independently verify a recorded directory:

```text
python -B -m scripts.eval_topologies --output-dir .cache/topology-evaluation
python -B -m scripts.eval_topologies --verify .cache/topology-evaluation/<run-id>
python -B -m unittest tests.test_topology_evaluation -v
```

The verifier opens snapshots read-only and reconstructs transitions, policy
decisions, actual deltas, final states, safety/utility verdicts, discovery counts,
and repair/false-block measurements. It requires no login, API credentials,
network access, or original runtime cache. Use the checkout matching the recorded
source commit. File hashes establish consistency, not independent authentication
of historical model origin or timings. No policy is promoted by this evaluation.
The public scenarios are inspectable and are not described as blinded held-out
evaluation. The historical single Codex discovery and the new random trials have
different repetition counts, so they support no claim of model superiority.
