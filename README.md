# SENTINEL-CG 2.0

Counterexample-guided safety for autonomous AI agents, built for the Meta Global AI Developer Hackathon 2026.

The target demonstration is a staging repair that indirectly starves production through shared capacity. Meta Red will explore permitted actions; Meta Blue will propose a policy repair. Independent deterministic code must verify both production safety and legitimate task completion, then replay the same actions under both policies.

The complete product source of truth is [docs/ENGINEERING_SPEC.md](docs/ENGINEERING_SPEC.md). Engineering rules are in [AGENTS.md](AGENTS.md).

## Current status

G1/G2 are implemented: real SQLite state, shared-capacity allocation, independent Safety/Utility Oracles, and a versioned Execution Gateway with deterministic preview, atomic commit and verifiable event traces. Evidence is a deterministic development fixture; there are no live Meta results. The existing MIT license and Git history are preserved. The old workflow bundle is disabled.

The deterministic G5 verification and controlled promotion path, G6 Safety Time Machine, and a minimal Evidence UI are implemented. The offline cycle rejects overblocking, verifies a narrow candidate against unchanged frozen regression and held-out suites, activates immutable v1, and executes the same recorded actions in two independent SQLite databases. A separate safe staging repair completes after promotion.

Offline inputs and results are labeled `deterministic_test_fixture`: the counterexample and patch are handcrafted, and no model is called. G3 and the AI-dependent parts of G4/G7 remain PENDING. The [historical failed Codex run](evals/evidence/codex-blocked.json) found no verified counterexample; its next inference request was rejected for cybersecurity access. Experimental Codex inference is disabled. Ordinary coding-agent development continues.

## Local setup

Use Python 3.10+ and Git. G1/G2 and the deterministic proof use the standard library. Install API/test dependencies with `python -m pip install -r requirements-dev.txt`, then run `python -m unittest discover -s tests -v` (including the English-only guard). G1/G2 remain reproducible with `python -m scripts.prove_g1` and `python -m scripts.prove_g2`.

Run `python -m scripts.prove_core` for candidate → independent verification → promotion/rejection → counterfactual replay. Its printed path identifies the complete bundle, immutable policy registry, verification artifacts, and real SQLite databases under `.cache/core/<run_id>/`. The bundle uses JSON/SHA-256; a PASS never establishes Meta-dependent gates. Frozen regression and held-out files must retain their recorded hashes.

For the Evidence UI, use the Node version supported by the pinned Vite dependency (this checkout was tested with Node 22.16). In `apps/web`, run `npm ci --ignore-scripts` and `npm run build`. From the repository root run `python -m scripts.serve_evidence`, then open `http://127.0.0.1:8000`. The UI can start a fresh offline proof and inspect saved states. SSE frames are labeled recorded execution. JSON downloads contain the actual bundle; the API checks bundle, verification, policy, and SQLite state integrity before serving it. See [docs/DEMO.md](docs/DEMO.md) for the truthful three-minute fixture walkthrough.

For future live integration, set `MODEL_API_KEY` locally using [.env.example](.env.example). Credentials have not been issued; model entitlement, tool calls, structured output, usage, and pricing remain unverified for this account. Budget settings do not authorize spending.

`python -m scripts.eval_codex` only records `PROVIDER_BLOCKED` with zero model calls and actions; there is no runtime re-enable flag or alternative transport. Provider blocking, transport failure, schema failure, and sandbox execution failure have separate machine-readable categories. The historical Codex evidence records an explicitly configured model ID without service confirmation and is not Meta evidence. Meta adapters require `MODEL_API_KEY` and have no substitute provider; paid Meta execution remains pending verified pricing and application-side spending guards.

Official API references: [quickstart](https://dev.meta.ai/docs/quickstart), [models](https://dev.meta.ai/docs/models). The documented base URL is `https://api.meta.ai/v1`; configured models are `muse-spark-1.3` and `muse-wonder-1.0`.

## Structure

- `apps/sandbox/`: deterministic world, allocator, transitions, preview, and fixtures.
- `apps/control/sentinel/`: Gateway, policies, independent oracles, evaluation, replay, evidence, agents, and API.
- `apps/web/`: the React/TypeScript/Vite evidence workspace.
- `evals/`: discovery, frozen regression, and held-out cases.
- `tests/`, `scripts/`, `prompts/`: added as the corresponding gates are implemented.

Only the controlled Promotion Gate can append v1 after independent verification succeeds. Models and HTTP clients have no activation endpoint. SQLite append-only guards protect history from accidental edits; a host process with database-write access remains trusted. G8, the final Meta demonstration, and the core release tag remain pending mandatory live gates.

MIT licensed; see [LICENSE](LICENSE).
