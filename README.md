# SENTINEL-CG 2.0

Counterexample-guided safety for autonomous AI agents, built for the Meta Global AI Developer Hackathon 2026.

The target demonstration is a staging repair that indirectly starves production through shared capacity. Meta Red will explore permitted actions; Meta Blue will propose a policy repair. Independent deterministic code must verify both production safety and legitimate task completion, then replay the same actions under both policies.

The complete product source of truth is [docs/ENGINEERING_SPEC.md](docs/ENGINEERING_SPEC.md). Engineering rules are in [AGENTS.md](AGENTS.md).

## Current status

G1/G2 are implemented: real SQLite state, shared-capacity allocation, independent Safety/Utility Oracles, and a versioned Execution Gateway with deterministic preview, atomic commit and verifiable event traces. Evidence is a deterministic development fixture; there are no live Meta results. The existing MIT license and Git history are preserved. The old workflow bundle is disabled.

The deterministic G5 verification and controlled promotion path, G6 Safety Time Machine, and a minimal Evidence UI are implemented. The offline cycle rejects overblocking, verifies a narrow candidate against unchanged frozen regression and held-out suites, activates immutable v1, and executes the same recorded actions in two independent SQLite databases. A separate safe staging repair completes after promotion.

Offline inputs and results are labeled `deterministic_test_fixture`: the counterexample and patch are handcrafted, and no model is called. G3 and the AI-dependent parts of G4/G7 remain PENDING. The [historical failed Codex run](evals/evidence/codex-blocked.json) found no verified counterexample; its next inference request was rejected for cybersecurity access. Experimental Codex inference is disabled. Ordinary coding-agent development continues.

The accepted offline version is tagged [`offline-core-v0.1`](https://github.com/Monisarek/sentinel-cg/tree/offline-core-v0.1) at `53b1aa9`, with a remote backup branch `codex/backup/offline-core-v0.1`. It is not the competition `core-v1.0.0` release. UI appearance is frozen while Meta-dependent gates remain pending.

## Local setup

Use Python 3.10+ and Git. G1/G2 and the deterministic proof use the standard library. Install API/test dependencies with `python -m pip install -r requirements-dev.txt`, then run `python -m unittest discover -s tests -v` (including the English-only guard). G1/G2 remain reproducible with `python -m scripts.prove_g1` and `python -m scripts.prove_g2`.

Run `python -m scripts.prove_core` for candidate → independent verification → promotion/rejection → counterfactual replay. Its printed path identifies the complete bundle, immutable policy registry, verification artifacts, and real SQLite databases under `.cache/core/<run_id>/`. The bundle uses JSON/SHA-256; a PASS never establishes Meta-dependent gates. Frozen regression and held-out files must retain their recorded hashes.

For the Evidence UI, use the Node version supported by the pinned Vite dependency (this checkout was tested with Node 22.16). In `apps/web`, run `npm ci --ignore-scripts` and `npm run build`. From the repository root run `python -m scripts.serve_evidence`, then open `http://127.0.0.1:8000`. The UI can start a fresh offline proof and inspect saved states. SSE frames are labeled recorded execution. JSON downloads contain the actual bundle; the API checks bundle, verification, policy, and SQLite state integrity before serving it. See [docs/DEMO.md](docs/DEMO.md) for the truthful three-minute fixture walkthrough.

The complete runtime command is `python -m scripts.eval_meta --authorization .cache/meta-budget.json --seed 0`. It runs Meta Red → independently verified counterexample → Meta Blue → candidate verification/promotion → fixed-trace replay → a fresh Red instance on active v1 with a different seed → a checkpointed JSON/SHA-256 bundle. Frozen cases never enter synthesis context. Failed discovery stops before Blue; rejected candidates stop before re-attack. No automatic transport retries or provider substitution occurs.

Before paid execution, obtain `MODEL_API_KEY`, verify current official model rates, context limits, billing semantics and entitlements, and obtain explicit approval for the budget. [evals/meta_budget.example.json](evals/meta_budget.example.json) is deliberately unauthorized and has no usable pricing. A local operator authorization file must supply all validated fields; configured caps do not authorize this coding agent to spend. Without a key or approval, the runtime writes a blocked PENDING bundle and sends no request.

Run `python -B -m scripts.check_meta_budget --config <operator-config.json>` before requesting paid execution. This local command makes zero HTTP requests, changes no authorization, and reports exact full-context reservations and blocking caps. The [dated official-model reference](evals/meta_model_reference.json) implies a $4.276224 Wonder reservation at 4096 completion tokens, exceeding the example $3 Blue cap. Caps remain unchanged. Numeric output ceilings, account entitlement, and actual API acceptance remain unverified; configuration feasibility is not a live service result.

The shared ledger is `.cache/meta-spend.sqlite3`; do not delete or replace it to reset limits. Exact Decimal caps apply per stage, cycle, UTC day and total spending, alongside stage/cycle call counts, output-token caps and absolute deadlines. Each request first reserves the full verified model input-context limit plus its maximum completion cost. Valid reported usage settles the conservative estimate. Failed or uncertain requests retain reservations; unexpected models or billing bounds block further spending. Prices and provider-reported token usage are not an invoice. Full-context reservations can reject small budgets even for short prompts; the runtime never increases approved caps automatically. HTTPX enforces a whole-request deadline, and expired responses cannot commit an action or promote policy.

`python -m scripts.prove_meta_runtime` exercises the same orchestration with explicit test-only drivers, synthetic rates, an isolated fixture ledger and network disabled. Its bundle is `deterministic_test_fixture`, `real_model_call=false`; AI-dependent gates stay PENDING. This command tests integration and must not be presented as live Meta discovery, synthesis, billing, or re-attack. No UI design changes accompany this runtime work.

`python -m scripts.eval_codex` only records `PROVIDER_BLOCKED` with zero model calls and actions; there is no runtime re-enable flag or alternative transport. Provider blocking, budget exhaustion, transport failure, schema failure, and sandbox execution failure have separate machine-readable categories. The historical Codex evidence records an explicitly configured model ID without service confirmation and is not Meta evidence. Genuine Meta tool calls, structured output, usage/billing semantics and latency remain unverified until permitted account access and budget approval exist.

Official API references: [quickstart](https://dev.meta.ai/docs/quickstart), [models](https://dev.meta.ai/docs/models). The documented base URL is `https://api.meta.ai/v1`; configured models are `muse-spark-1.3` and `muse-wonder-1.0`.

## Structure

- `apps/sandbox/`: deterministic world, allocator, transitions, preview, and fixtures.
- `apps/control/sentinel/`: Gateway, policies, independent oracles, evaluation, replay, evidence, agents, and API.
- `apps/web/`: the React/TypeScript/Vite evidence workspace.
- `evals/`: discovery, frozen regression, and held-out cases.
- `tests/`, `scripts/`, `prompts/`: added as the corresponding gates are implemented.

Only the controlled Promotion Gate can append v1 after independent verification succeeds. Models and HTTP clients have no activation endpoint. SQLite append-only guards protect history from accidental edits; a host process with database-write access remains trusted. G8, the final Meta demonstration, and the core release tag remain pending mandatory live gates.

MIT licensed; see [LICENSE](LICENSE).
