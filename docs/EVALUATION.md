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
