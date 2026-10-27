# SENTINEL-CG 2.0

Counterexample-guided safety for autonomous AI agents, built for the Meta Global AI Developer Hackathon 2026.

The target demonstration is a staging repair that indirectly starves production through shared capacity. Meta Red will explore permitted actions; Meta Blue will propose a policy repair. Independent deterministic code must verify both production safety and legitimate task completion, then replay the same actions under both policies.

The complete product source of truth is [docs/ENGINEERING_SPEC.md](docs/ENGINEERING_SPEC.md). Engineering rules are in [AGENTS.md](AGENTS.md).

## Current status

Preflight and repository bootstrap only. No acceptance gate G1–G8 has passed yet; there are no live Meta results or runnable demo. The existing MIT license and initial Git history are preserved. No previous implementation was present to reuse, extend, or retire. The old workflow bundle is disabled.

Next gate: **G1 — Causal failure exists**, proving both harmful and safe staging repair paths against real sandbox state and independent oracles. No UI before the proof loop works.

## Local setup

Python 3.10+ and Git are sufficient to begin the offline deterministic core. Create an isolated environment with `python -m venv .venv`; the preflight environment has already been created locally. Dependencies and runnable commands will be added with their implementing gate. Docker and Node are needed for later service packaging and UI work.

For future live integration, set `MODEL_API_KEY` locally using [.env.example](.env.example). Credentials have not been issued; model entitlement, tool calls, structured output, usage, and pricing remain unverified for this account. Budget settings do not authorize spending.

Official API references: [quickstart](https://dev.meta.ai/docs/quickstart), [models](https://dev.meta.ai/docs/models). The documented base URL is `https://api.meta.ai/v1`; configured models are `muse-spark-1.3` and `muse-wonder-1.0`.

## Structure

- `apps/sandbox/`: deterministic world, allocator, transitions, preview, and fixtures.
- `apps/control/sentinel/`: Gateway, policies, independent oracles, evaluation, replay, evidence, agents, and API.
- `apps/web/`: reserved for the evidence UI after the core works.
- `evals/`: discovery, frozen regression, and held-out cases.
- `tests/`, `scripts/`, `prompts/`: added as the corresponding gates are implemented.

These directories are scaffolding, not implemented capabilities.

MIT licensed; see [LICENSE](LICENSE).
