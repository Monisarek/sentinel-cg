# SENTINEL-CG 2.0

Counterexample-guided safety for autonomous AI agents, built for the Meta Global AI Developer Hackathon 2026.

The target demonstration is a staging repair that indirectly starves production through shared capacity. Meta Red will explore permitted actions; Meta Blue will propose a policy repair. Independent deterministic code must verify both production safety and legitimate task completion, then replay the same actions under both policies.

The complete product source of truth is [docs/ENGINEERING_SPEC.md](docs/ENGINEERING_SPEC.md). Engineering rules are in [AGENTS.md](AGENTS.md).

## Current status

G1 is implemented: real SQLite state, shared-capacity allocation, staging-only harmful and safe repairs, and independent Safety/Utility Oracles. Evidence is a deterministic development fixture; there are no live Meta results. The existing MIT license and Git history are preserved. The old workflow bundle is disabled.

G2 and later gates remain pending. No UI before the proof loop works.

## Local setup

Python 3.10+ and Git suffice; G1 uses only the standard library. From the repository root, run `python -m unittest discover -s tests -v`, then `python -m scripts.prove_g1`. The proof prints actual calls and verdicts and preserves both SQLite databases and JSON evidence under `.cache/g1/<run_id>/`.

For future live integration, set `MODEL_API_KEY` locally using [.env.example](.env.example). Credentials have not been issued; model entitlement, tool calls, structured output, usage, and pricing remain unverified for this account. Budget settings do not authorize spending.

Official API references: [quickstart](https://dev.meta.ai/docs/quickstart), [models](https://dev.meta.ai/docs/models). The documented base URL is `https://api.meta.ai/v1`; configured models are `muse-spark-1.3` and `muse-wonder-1.0`.

## Structure

- `apps/sandbox/`: deterministic world, allocator, transitions, preview, and fixtures.
- `apps/control/sentinel/`: Gateway, policies, independent oracles, evaluation, replay, evidence, agents, and API.
- `apps/web/`: reserved for the evidence UI after the core works.
- `evals/`: discovery, frozen regression, and held-out cases.
- `tests/`, `scripts/`, `prompts/`: added as the corresponding gates are implemented.

Only the G1 paths above are implemented; agent, Gateway, replay, and UI directories remain scaffolding.

MIT licensed; see [LICENSE](LICENSE).
