# Production-readiness gates

## Release claim

SENTINEL-CG currently demonstrates **a bounded, offline-verifiable agent-safety experiment**, not a deployed, independently audited production security control. A verified cycle means that its recorded, limited evaluation passed for the modeled environment. It does not establish universal resistance to agent errors or adversaries.

This file defines the release boundary for a **public engineering portfolio**. A separate architectural and security review is required before a real organization would rely on it to authorize production changes. No model-generated decision may promote policy without independent host-side verification.

## Implemented foundations

- Gateway validates typed proposals, performs deterministic preview, enforces policy, and commits versioned SQLite state atomically.
- Trusted task requirements and approvals are independent of the Red/Blue model's output.
- Candidate policies use a restricted, deny-only DSL and must pass counterexample, utility, regression and held-out verification.
- Separate SQLite forks replay the identical trace under the original and candidate policy. Fresh adaptive re-attack is separately recorded.
- Portable evidence packages can be verified offline from a pinned source bundle. Hashes provide integrity **relative to trusted inputs**, not third-party attestation.
- The local workspace binds loopback by default. The optional `--read-only` flag rejects both execution endpoints while allowing saved evidence access.
- GitHub Actions runs offline backend tests, a source-language check, frontend tests and the production build across Windows and Linux/Python variants. **Check actual results before claiming this gate passed.**

## Release gates

| Gate | Required acceptance evidence | Current assessment |
| --- | --- | --- |
| R0: continuous validation | Green GitHub Actions checks on a clean candidate commit: Python tests, frontend tests, TypeScript build and public-source check. No model login or paid API is needed. | CI configured on hardening branch; run results must be inspected |
| R1: execution boundary | Local execution endpoints remain loopback-only. Read-only mode cannot invoke model calls or mutate sandbox state. No public runner exposure without authentication, authorization and CSRF review. | Local-only boundary with read-only switch; production identity/access controls NOT IMPLEMENTED |
| R2: provenance | Clean source, pinned verifier, offline portable bundle, independent verifier output, and publication of a trusted checksum or signed attestation via a separate trusted channel. | Offline integrity is implemented; external authenticity/attestation NOT ESTABLISHED |
| R3: adversarial validity | Multiple independently chosen scenarios, varied topologies, genuinely held-out tests, preserved failures, explicit false-positive/false-negative and utility metrics, baseline comparisons. No overclaim from one successful cycle. | Single bounded live Codex success plus parameterized/offline baselines; cross-topology generalization NOT ESTABLISHED |
| R4: failure recovery | Fault-injection evidence for crashes, aborts, timeouts, invalid model output, stale state, repeated execution and concurrent attempts. Recovery is safe and idempotent. | Several negative tests exist; crash/recovery audit NOT COMPLETE |
| R5: operator safety | Model execution in a restricted operating-system identity/sandbox, secret isolation, resource/cost limits, run audit trail, secure retention and deletion, and host isolation from untrusted project files. | **BLOCKED:** CLI subprocess runs under host account, not a complete OS security boundary |
| R6: reproducible release | Source tag, pinned dependencies, clean CI, versioned protocol/fixtures, release notes, reproducible launch and evidence verification instructions. No secret material or private account logs. | Documentation and frozen inputs exist; final release candidate NOT CUT |
| R7: public proof | Short end-to-end demo, architecture/trust diagram, clearly labeled model-generated vs host-verifiable claims, baseline results, limitations and one failure walkthrough. | Case study exists; final walkthrough/published release pending |

**Release rule:** R0-R2 and R4-R7 must be explicitly signed off for the advertised deployment scope. R3 determines the strength of any empirical safety claims. A public, read-only demonstration is a different and narrower security claim than a multi-tenant production service. Never use a portfolio PASS as authorization for an actual infrastructure restart.

## Prioritized technical work

1. **P0 — Isolate inference from the operator account.** Separate runner and verifier processes with OS-level identity, file and network restrictions; disable arbitrary external execution and keep secrets out of model-proposed content. Demonstrate a failed escape attempt, safe cancellation, and non-exposure of host credentials. The existing CLI option flags do not by themselves establish isolation.
2. **P0 — Avoid exposing an unauthenticated runner.** Keep `127.0.0.1` binding until identity, request authorization, CSRF prevention, and per-user budgets have been designed and tested. The read-only switch is a narrow safeguard, not a substitute for an authentication system.
3. **P1 — Durable execution lifecycle.** Use persisted run states, atomic claims, idempotency keys, cancellation, retry policy and crash recovery; test two concurrent processes rather than only threads in one server process.
4. **P1 — Broaden evaluation.** Test at least three structurally different resource topologies, preserve blinded or unpublished evaluation sets where possible, and publish denominators, uncertainty, baselines and observed failures. Do not reclassify partial or inconclusive runs as successes.
5. **P1 — Protect and attest evidence.** Set size/count caps for inputs and logs, define cleanup policy, and add out-of-band signed release metadata (or trusted checksum delivery). Verify that no credentials or personally identifying data enter retained prompts, diagnostics or archives.
6. **P2 — Operational delivery.** Add health/readiness metrics, controlled shutdown, a documented upgrade path and a container/restricted service layout suitable for an explicitly defined trust model. Avoid Kubernetes or multi-tenancy without a real requirement.

## Local validation commands

From the repository root, with Python dependencies installed:

```text
python -B -m unittest discover -s tests -v
python -B -m scripts.check_english
npm --prefix apps/web ci --ignore-scripts
npm --prefix apps/web test
npm --prefix apps/web run build
```

For read-only inspection after preparing saved evidence:

```text
python -m scripts.serve_evidence --read-only --output-dir .cache/portfolio
```

The process still binds to `127.0.0.1`. POST requests to both `/api/runs` and `/api/codex-runs` must return HTTP 403 in read-only mode; GET for an existing verified run remains available. The output directory may also include saved Codex cycles when the standard portfolio layout is used. Do not publish private run artifacts or bind the server to a public interface.

## Change-control policy

Do not merge into `main`, publish a release tag, claim compliance certification, or label the system production-safe on the strength of this checklist alone. Keep evidence of each gate in reviewable CI logs, tests, source commits, portable bundles and documented threat assumptions.
