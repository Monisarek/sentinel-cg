# Evidence remediation audit

The complete independent report (F1–F18) was compared with `build` at
`992172bfdfa3cefb5eb99b40bc0909921bb86d51` and remediation through source commit
`4446dd3d4e84f2cc0de865421a34bdcb76527f7b` on
October 29, 2026. The report initially inspected `bcb0d9c`; its findings are
claims to check, not evidence that every issue remains present. Historical raw
evidence and frozen regression/held-out fixtures are preserved.

`ALREADY_FIXED` identifies a correction present at the inspected commit.
`NOT_REPRODUCED` means the claimed behavior was not established in this workload.
`DEFERRED` identifies work intentionally excluded from this sprint. A working-tree
correction is identified by its source commit and verification below.

| Finding | Priority | Disposition and evidence |
| --- | --- | --- |
| F1: pipeline completion can mask a fresh bypass | P0 | **ALREADY_FIXED** in `ecbef35` and `6cf850e`. `assess_re_attack()` separates execution, outcome, and assurance. `test_fresh_real_sqlite_bypass_cannot_report_overall_pass_after_verified_promotion` executes an actual bypass after promotion and requires overall FAIL/BYPASSED. Partial unsafe event history also wins over an unfinished aggregate. Additional portable-verifier tests reject rehashed and legacy false PASS claims. |
| F2: Red receives solution hints | P0 | **ALREADY_FIXED** in `b61e68e`. [Neutral live-v1](../evals/protocol/live-v1.json) binds prompts, tool descriptions, schemas, models, seeds, and limits before live calls. `test_primary_prompts_expose_contract_and_objective_without_solution_hints` checks the actual runtime inputs. The trusted contract remains visible; empirical prompt effects remain unmeasured without Meta access. |
| F3: direct invariant shield baseline is absent | P0 | **ALREADY_FIXED** in `992172b`. Run `direct-shield-08422d91385a4a96a417479a07c98a6f` blocks 5/5 unsafe cases and completes 20/20 required utility workflows with zero false blocks, matching handcrafted fixture v1. Genuine Blue comparison remains PENDING. See [Evaluation](EVALUATION.md). |
| F4: random explorer baseline is absent | P0 | **ALREADY_FIXED** in `992172b`. Protocol and seeds 0–99 were committed before measurement. Run `random-baseline-d1780f0fef7847f19d95dab60de91f40` finds 84/100 verified counterexamples within ten actions; all 100 attempts, including 16 action-limit exhaustions, are retained. No Meta superiority is established. |
| F5: held-out cases retain the same topology | P1 | **CONFIRMED**, with same-topology coverage measured. Protocol/code committed in `397e3d9` before run `parametric-sweep-1a68ec8119204235b9b613778f917fa4`: 100/100 valid scenarios reproduce S1 under v0, fixture v1 blocks 100/100, utility completes 500/500 workflows, and DENY mutations are zero. New worlds and cross-topology claims are **DEFERRED**. Frozen suites remain unchanged. |
| F6: Blue is told the intended comparison strategy | P0 | **ALREADY_FIXED** in `b61e68e`. Frozen neutral Blue and validator-correction prompts retain the restricted DSL and utility requirements without prescribing the desired inequality. The same static prompt test covers Blue. Actual synthesis quality remains unmeasured. |
| F7: the example Wonder cap cannot reserve a request | P0 | **ALREADY_FIXED** detection in `e486000`, with authorization/protocol binding in `b61e68e`. The zero-network budget preflight reports a $4.276224 Wonder reservation at 4096 completion tokens against the unchanged $3 example cap. `test_unknown_model_prices_and_protocol_limits_stop_before_ledger_or_transport` verifies refusal before spending. A usable key and explicitly approved feasible budget remain real live blockers. |
| F8: transient failures have no bounded retry | P2 | **DEFERRED**. Inspection confirms no automatic retry and 429 currently maps to provider blocking. No live transient failure has been observed. Adding paid retries before transport/billing validation is not justified for this sprint; failures remain explicit and fail closed. Rejected Codex inference must never be retried or bypassed. |
| F9: Chat Completions may weaken multi-turn reasoning | P2 | **NOT_REPRODUCED** in this workload: no permitted Meta run exists to compare. Meta documents Chat Completions and recommends Responses for reasoning continuity. Adapter migration and A/B calls are **DEFERRED** until an observed limitation and approved budget justify them. |
| F10: evidence cannot be verified away from the original cache | P1 | **ALREADY_FIXED** export/verification code in `a5b6605`. The [portable package](VERIFY_EVIDENCE.md) preserves raw JSON, allowlisted artifacts, SQLite snapshots, source, and SHA-256 manifest. Tests independently recompute transitions, deltas, oracles, policy/promotion bindings, provenance, and partial-run claims. Clean-checkout acceptance is recorded separately; source `.cache` is preserved, not deleted. |
| F11: a commit SHA can hide uncommitted execution changes | P1 | **ALREADY_FIXED** in `4446dd3`. `--competition` rejects dirty source evidence or exporter checkout before copying; default development exports retain dirty provenance. Negative tests and four positive clean-checkout exports pass. Export grade never converts fixtures or PENDING gates into live evidence. |
| F12: main, draft PR, and final release remain unfinished | P1 | **DEFERRED final release**, as required by the accepted gate order. The [release checklist](DEMO.md#release-checklist) requires real Meta gates, clean evidence, tests/build, final demo/submission, then merge/tag/protected backup. Preserving `main` and the draft before those gates is deliberate. Neither a local checklist nor an offline fixture establishes G8. No new CI infrastructure is required for this sprint. |
| F13: numerical judging weights lack provenance | P0 | **ALREADY_FIXED** in `4446dd3`. The participant-supplied accepted Official Rules explicitly give 30% Impact, 25% Use of Meta AI Tooling, 20% Technical Depth & Execution, and 25% Demo Quality. Only the corresponding specification/demo sections change. The public organizer page separately supports qualitative Indie criteria, not these numerical weights. |
| F14: related systems undermine an architecture-first novelty claim | P1 | **NOT_REPRODUCED** as an unsupported “first” or research-novelty claim in current public wording. The related-work concern is valid; README now acknowledges VEPA, ActGov, and AutoSpec and describes the integrated executable evidence contribution without comparative performance claims. No architectural rewrite is warranted. |
| F15: ownership of the safety requirement is unclear | P1 | **ALREADY_FIXED** wording in `4446dd3`. `TaskContract.production_minimum` is trusted host input; `evaluate_safety()` checks committed allocation against it. README and UI identify the trusted safety contract. The UI never derives its threshold from a model explanation; absent thresholds remain UNVERIFIED. Models search for enforcement counterexamples, not unknown safety requirements. An unfamiliar-engineer demo review remains unperformed. |
| F16: guarantees depend on the deterministic model's fidelity | P1 disclosure | **ALREADY_DISCLOSED** as a synthetic environment in the specification; README now makes the limitation explicit. Preview/commit consistency proves this model's semantics, not cloud fidelity. Real-state calibration, digital twins, and deployment controllers are **DEFERRED** outside scope. |
| F17: the implementation supports one repair, not continual evolution | P1 disclosure | **ALREADY_FIXED** behavior: the registry permits one bounded `v0 → v1` cycle. `test_second_promotion_and_unknown_version_cannot_bypass_gate` rejects a second promotion. README explicitly limits the claim; multi-version evolution is **DEFERRED** outside scope. |
| F18: participant eligibility is not independently confirmed | External | **UNVERIFIED** administrative dependency. Technical code and repository history cannot prove accepted participant details meet the Official Rules. No conclusion is inferred from language, username, nationality, or timezone, and no personal eligibility information belongs in the public repository. |

## Recorded evidence and remaining limits

Source `4446dd3` passed 221 Python tests, 18 frontend tests, and the production
frontend build. Four portable packages were produced from a separate clean
checkout and verified from the exported Git bundle in another clean checkout:
core, full runtime fixture, blocked runtime, and an actual fixture re-attack
bypass. Standard-library-only verification ran with no key, network access and
both original caches forbidden, and a read-only file-open guard. Package and
checkout hashes were unchanged. See [portable evidence](VERIFY_EVIDENCE.md).

Both baseline runs above use committed code at `992172b`; their source dirty flag
remains disclosed. Their policy patch is handcrafted fixture evidence. The direct
shield result demonstrates that LLM synthesis is not mathematically necessary for
this bounded world. Random search is effective here. The project claim therefore
centers on independently verified repair, utility preservation, and causal replay,
not unique discovery ability. Full measurements and retained attempt references
are in [Evaluation](EVALUATION.md).

No genuine Meta calls, measured model comparison, production fidelity validation,
final competition video, or checked submission are established by this audit.
G3, AI-dependent G4/G7, and G8 remain PENDING. The accepted offline anchor remains
`offline-core-v0.1`; no competition core release is declared.

Official API references reviewed for the implementation are [model IDs and context
windows](https://dev.meta.ai/docs/models), [Standard-mode prices](https://dev.meta.ai/docs/pricing-rate-limits),
[Chat Completions](https://dev.meta.ai/docs/protocols/chat-completions), [tool calling](https://dev.meta.ai/docs/tool-calling),
and [structured output](https://dev.meta.ai/docs/structured-output). Account entitlement,
numeric output ceilings, real billing behavior, latency, and transport behavior
remain unverified. The dated model reference does not authorize spending.

Judging-weight provenance is the participant-provided accepted Official Rules.
The [public organizer page](https://events.cerebralvalley.ai/e/meta-ai-developer-hackathon)
separately confirms the Indie considerations of creativity, technical ambition,
and actual functionality. Personal eligibility is outside this technical audit.
