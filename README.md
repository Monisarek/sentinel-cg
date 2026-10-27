# SENTINEL-CG 2.0

Counterexample-guided safety for autonomous AI agents, built for the Meta Global AI Developer Hackathon 2026.

The target demonstration is a staging repair that indirectly starves production through shared capacity. Meta Red will explore permitted actions; Meta Blue will propose a policy repair. Independent deterministic code must verify both production safety and legitimate task completion, then replay the same actions under both policies.

The complete product source of truth is [docs/ENGINEERING_SPEC.md](docs/ENGINEERING_SPEC.md). Engineering rules are in [AGENTS.md](AGENTS.md).

## Current status

G1/G2 are implemented: real SQLite state, shared-capacity allocation, independent Safety/Utility Oracles, and a versioned Execution Gateway with deterministic preview, atomic commit and verifiable event traces. Evidence is a deterministic development fixture; there are no live Meta results. The existing MIT license and Git history are preserved. The old workflow bundle is disabled.

G3 and later gates remain pending. Temporary Codex and Meta adapters, the restricted deny-only DSL, exact scoped approvals, and deterministic candidate verification are implemented and unit tested. A genuine provisional Codex run made two staging configuration changes, then received a cybersecurity access rejection from the provider. It found no verified counterexample; Blue synthesis, live promotion, and adaptive re-attack have not completed. The recorded [failed run](evals/evidence/codex-blocked.json) is Codex evidence, with Meta confirmation pending. No UI before the proof loop works.

## Local setup

Python 3.10+ and Git suffice; G1/G2 use only the standard library. Run `python -m unittest discover -s tests -v`, then `python -m scripts.prove_g1` or `python -m scripts.prove_g2`. Proofs preserve actual SQLite databases, Gateway traces and JSON evidence under `.cache/g1/` and `.cache/g2/`.

For future live integration, set `MODEL_API_KEY` locally using [.env.example](.env.example). Credentials have not been issued; model entitlement, tool calls, structured output, usage, and pricing remain unverified for this account. Budget settings do not authorize spending.

The temporary development command is `python -m scripts.eval_codex`; it uses the official installed CLI and existing login, returns a nonzero status when discovery fails, and preserves every run under `.cache/codex/`. Provider refusals stop the run without automatic retries. Do not rerun a rejected experiment until the provider grants the required access. Red and Blue use separate ephemeral sessions with native tools disabled. The CLI process runs as the host user, so this restriction is not complete OS isolation. Codex records the explicitly configured model ID; its JSON event stream does not confirm the service-returned model ID. Meta adapters require `MODEL_API_KEY` and have no substitute provider; paid Meta execution remains pending verified pricing and application-side spending guards.

Official API references: [quickstart](https://dev.meta.ai/docs/quickstart), [models](https://dev.meta.ai/docs/models). The documented base URL is `https://api.meta.ai/v1`; configured models are `muse-spark-1.3` and `muse-wonder-1.0`.

## Structure

- `apps/sandbox/`: deterministic world, allocator, transitions, preview, and fixtures.
- `apps/control/sentinel/`: Gateway, policies, independent oracles, evaluation, replay, evidence, agents, and API.
- `apps/web/`: reserved for the evidence UI after the core works.
- `evals/`: discovery, frozen regression, and held-out cases.
- `tests/`, `scripts/`, `prompts/`: added as the corresponding gates are implemented.

Candidate verification uses frozen regression and held-out suites and real isolated SQLite execution. Its tests use explicitly handcrafted patches and establish verifier behavior only. A verification result never activates a candidate. Replay and UI remain pending.

MIT licensed; see [LICENSE](LICENSE).
