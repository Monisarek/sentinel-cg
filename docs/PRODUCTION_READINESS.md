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
- The documented server binds to loopback. `--read-only` rejects every execution/cancellation endpoint and does not create lifecycle files. GET and original JSON downloads remain available.
- A local kernel file lock coordinates complete experiments across processes. A separate SQLite journal persists claims, idempotency, lifecycle, cancellation, errors and stage timings. Promotion idempotency is enforced in the registry transaction.
- The bounded subprocess supervisor limits input/output, CPU, memory and time. Windows Job Object assignment precedes child resume; process-tree cleanup on timeout, cancellation and supervisor crash is tested. These are resource controls, not OS identity or network isolation.
- GitHub Actions runs offline backend tests, a source-language check, frontend tests and the production build across Windows and Linux/Python variants. **Check actual results before claiming this gate passed.**

## Release gates

| Gate | Required acceptance evidence | Current assessment |
| --- | --- | --- |
| R0: continuous validation | Green GitHub Actions checks on a clean candidate commit: Python tests, frontend tests, TypeScript build and public-source check. No model login or paid API is needed. | **PASS** at `18aad82`: all five jobs, Windows/Linux with Python 3.10/3.12 and frontend, workflow `42269346164`. A final release still requires green checks on its exact HEAD. |
| R1: execution boundary | Local execution endpoints remain loopback-only. Read-only mode cannot invoke model calls or mutate sandbox state. No public runner exposure without authentication, authorization and CSRF review. | Local-only boundary with read-only switch; production identity/access controls NOT IMPLEMENTED |
| R2: provenance | Clean source, pinned verifier, offline portable bundle, independent verifier output, and publication of a trusted checksum or signed attestation via a separate trusted channel. | Offline integrity is implemented; external authenticity/attestation NOT ESTABLISHED |
| R3: adversarial validity | Multiple independently chosen scenarios, varied topologies, genuinely held-out tests, preserved failures, explicit false-positive/false-negative and utility metrics, baseline comparisons. No overclaim from one successful cycle. | Three structural deterministic worlds and independent verifier implemented. Only original shared capacity has a recorded model run. Model-driven cross-topology generalization and independently blinded evaluation NOT ESTABLISHED. |
| R4: failure recovery | Fault-injection evidence for crashes, aborts, timeouts, invalid model output, stale state, repeated execution and concurrent attempts. Recovery is safe and idempotent. | Local Windows lifecycle/kernel-lock, cancellation, SQLite rollback and duplicate promotion negative tests implemented; external production deployment validation NOT PERFORMED. Linux supervisor SIGKILL limitation below. |
| R5: operator safety | Model execution in a restricted operating-system identity/sandbox, secret isolation, resource/cost limits, run audit trail, secure retention and deletion, and host isolation from untrusted project files. | Windows resource containment tested. **BLOCKED:** separate OS identity/filesystem/network isolation is not provisioned; CLI still authenticates under the operator account. |
| R6: reproducible release | Source tag, pinned dependencies, clean CI, versioned protocol/fixtures, release notes, reproducible launch and evidence verification instructions. No secret material or private account logs. | Documentation and frozen inputs exist; final release candidate NOT CUT |
| R7: public proof | Short end-to-end demo, architecture/trust diagram, clearly labeled model-generated vs host-verifiable claims, baseline results, limitations and one failure walkthrough. | Case study, architecture/trust diagram and 2-3 minute recorded-run walkthrough prepared. Video publication and owner release review PENDING. |

**Release rule:** Portfolio readiness means reproducible public evidence, tests, documentation, explicit limits and a reviewed release. It does not require or imply a public execution service. Production deployment readiness additionally requires effective OS/credential/network isolation, authenticated access, operational recovery and deployment acceptance. R5 remains a production blocker. No release or `main` merge is authorized by this document. Never use a portfolio PASS as authorization for an actual infrastructure restart.

## Threat model and authority

| Boundary | Authority and enforcement | Residual limit |
| --- | --- | --- |
| Host configuration and safety contract | Operator-owned seed, minima, exact approval scope, protocol and budgets; no proposed action may change them | Host code and local file owners are trusted |
| Model output and external content | Untrusted bounded proposal data; typed parser, closed DSL, Gateway and independent verification | A model explanation is never a final safety or utility verdict |
| Native execution privileges | Shell, filesystem, browser, network tools and MCP disabled in model-facing CLI configuration; output tool attempts rejected | CLI itself still has the operator's identity and network access |
| Sandbox state | Versioned SQLite transactions; identical pure transition for preview/commit; zero mutation on DENY | Synthetic deterministic systems, not actual infrastructure |
| Promotion | Independent safety/utility/regression/held-out checks and immutable registry receipt; transactional idempotency | One repair generation; host DB writers remain trusted |
| Local browser/API | Loopback binding, Host and peer checks, same-origin execution checks, idempotent requests; read-only rejects mutation routes | No multi-user authentication; do not expose or reverse-proxy the runner |
| Evidence | Bounded regular artifacts, secret-pattern screening, immutable originals and independent read-only verification | Hashes establish consistency relative to inputs, not provider authenticity; historical paths remain disclosed metadata |

No credential is copied into a request directory. The CLI uses its existing
authentication store; SENTINEL never reads or extracts tokens. Native tools are
disabled before the process receives the prompt. New diagnostics retain prompt
and schema hashes, error categories and return codes, not raw prompts or raw
provider stderr. Model proposal records remain part of the explicit evidence.

## OS isolation blocker and tested resource controls

The inspected host is Windows 10 build 19045 with a medium-integrity operator
process. No separate restricted runner identity and independently authenticated
profile is provisioned. This sprint did not create users, copy credentials,
alter ACLs/firewall policy or run an elevated sandbox setup. Doing so requires
operator provisioning and separate filesystem/network denial acceptance tests.
The native Windows sandbox documentation describes administrator-approved
lower-privilege users and firewall policy; enabling a flag alone does not isolate
the whole authenticated CLI supervisor.
[Official Windows sandbox documentation](https://learn.chatgpt.com/docs/windows/windows-sandbox)

The resource supervisor starts the Windows child suspended, assigns it to a
Job Object, then resumes it. Limits: 8 active processes; 1 GiB per process and
2 GiB aggregate committed memory; aggregate CPU time capped at the rounded-up
call timeout; wall-clock timeout; 2,000,000 bytes each for input and combined
stdout/stderr; structured answer capped at 2,000,000 bytes. Job closure kills
descendants, including when the supervisor process crashes. Python child tests
verify those controls without using a model. The installed official
`codex-cli 0.166.0-alpha.6` passed a supervised `--version` smoke test.

POSIX uses a new process group, address-space/CPU limits and tree cleanup on
controlled exit, timeout and cancellation. A supervisor SIGKILL or a descendant
that creates a new session can escape that cleanup. No POSIX process-count or
aggregate-memory security claim is made. A production Linux runner needs an
external service supervisor/cgroup and a restricted identity. Network shares,
hostile local processes, host compromise and distributed workers are outside the
kernel-lock coordination guarantee.

The modified runner has **not** been validated with another real model request:
the owner prohibited new inference without specific approval. The original real
run remains unmodified and is evidence for its original source, not for new
resource limits or recovery behavior.

## Durable operation and failure semantics

`evaluation/lifecycle.py` stores `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`,
`CANCELLED` and `INCONCLUSIVE`. The kernel lock is held throughout a run and
released automatically on process death. On startup or a new claim, unfinished
work becomes `INCONCLUSIVE / INTERRUPTED` only after obtaining that lock. It is
never resumed or automatically re-issued to the provider. Cancellation requests
persist separately and are checked before model calls and Gateway commits; the
subprocess supervisor polls them during authentication and inference.

SQLite operations have bounded busy timeouts; cancellation during deterministic
verification takes effect at its next budget/commit boundary. It is cooperative,
not instantaneous. A cancelled lifecycle cannot serve a bundle as PASS. Explicit
new requests are distinct experiments; ambiguous retries must reuse the same
`Idempotency-Key`. The browser retains that key across network/5xx failures.
Clients omitting the header request a new unique attempt on every POST.

Both the CLI subscription command and API Codex endpoint use the same journal
and lock for a given output root. Standard portfolio/core viewing shares
`.cache/codex-cycles/.executions.sqlite3`. Custom roots intentionally coordinate
only with users of that same root. Separate sandbox states do not share state.
Promotion request keys bind the candidate, initial state, task, proposal trace
and evidence kind in one registry transaction; retries return the original receipt.

| Interface | Behavior |
| --- | --- |
| `POST /api/codex-runs` | Explicit bounded subscription execution; optional `Idempotency-Key` |
| `POST /api/runs` | Deterministic fixture; same local execution lock |
| `GET /api/executions/RUN_ID` | Read-only persisted lifecycle, errors, correlation ID and stage timings |
| `POST /api/executions/RUN_ID/cancel` | Same-origin local cancellation; rejected in read-only mode |
| `GET /api/runs/RUN_ID` and `/download` | Verified saved evidence and original bytes; no inference |

The journal accepts at most 10,000 attempts and then refuses new work. It does
not silently delete idempotency history. Archive it only while all runners are
stopped; keep the original journal and never retry old keys against a new empty
journal. Existing evidence retention/file-size rules are documented in
[verification instructions](VERIFY_EVIDENCE.md#resource-privacy-and-retention-boundaries).
No automatic transport retries or automatic evidence deletion are enabled.

## Verification map

| Requirement | Reproducible evidence |
| --- | --- |
| A1/A3: unsafe data, authorization, no mutation, invalid patch and stale state | `test_gateway`, `test_policy_dsl`, `test_approvals`, `test_storage_security`; real SQLite readback and byte checks |
| A2: bounded child resources, timeout/cancellation/tree cleanup | `test_process_limits`; ordinary offline Python children; OS identity/network isolation remains BLOCKED |
| A4: read-only has no execution or lifecycle side effects; spoofed peer/Origin/Host rejected | `test_evidence_api`; byte snapshots before/after; transports patched to fail if invoked |
| B1-B4: duplicate requests, two-process contention, crash/restart, cancelled completion and storage errors | `test_execution_lifecycle`, `test_storage_security`, `test_evidence_api`; spawned OS processes and transaction fault injection |
| Model timeout/refusal/schema failure | `test_codex_provider`, `test_codex_subscription`, `test_meta_runtime`, `test_agent_loop`; explicit transport fixtures, not genuine model tests |
| C: structural coverage and matched deterministic baselines | `test_topology_evaluation`, declared protocol and independent recorded-run verifier; no new inference |
| D: integrity, bounds, private-data screening and offline read-only verification | `test_portable_evidence`; original ZIPs unchanged; provider authenticity explicitly not independently established |
| E: correlation, stage/tool timing, decisions, policy hashes, budgets and cancellation history | Execution journal, hash-covered Gateway `duration_seconds`, existing model latency and budget records |

The archive privacy review found no known credential patterns or email-like
values across the two preserved collections. Historical absolute path values
remain: 15 in the Codex collection and 31 in the earlier collection. They may
identify the original workstation account and are inert, not runtime
dependencies. Original evidence is not rewritten. Pattern scanning is not a
privacy certification of arbitrary Git history or binary contents.

The clean structural evaluation at `7285489`, run
`topology-evaluation-5b5af64258bb4821b6d7fe77bb58f636`, retained and independently
verified all 230 attempts. Random v0 discovery was 19/20 shared-capacity,
14/20 schema-dependency, and 10/20 deferred-maintenance trials. Shield and
fixture candidates had 0/20 observed violations per world and preserved each
declared legitimate workflow. The recorded original model patch was replayed
only on shared capacity. [Exact measurements, denominators and limits](EVALUATION.md#recorded-structural-evaluation)

The new package contains zero detected absolute-path/email values or known
credential patterns in its JSON and SQLite artifacts. Its separate source-bundle
checkout verifier passed with network, file writes, third-party site packages,
credentials and original-cache reads unavailable; all artifact and verifier
bytes stayed unchanged. The old collections retain their historical path
metadata and original hashes.

## Local validation commands

Local candidate validation: **290 Python tests PASS** (207.710 seconds),
**24 frontend tests PASS**, English-only guard PASS, TypeScript and Vite build
PASS on Windows/Python 3.10.11. The restricted tool sandbox denied Vite's child
process spawn (`EPERM`); rerunning the identical build with ordinary host
permissions passed. No platform or check was removed. Independent reviewers
also ran 29 portable-evidence tests and 12 topology tests successfully. These
are offline unit/integration/recorded-evidence checks, not real-inference or
production deployment acceptance.

[GitHub Actions run `42269346164`](https://github.com/Monisarek/sentinel-cg/actions/runs/42269346164)
passed all five jobs at `18aad82a7f092182af62719b330ad60ffe687d99`.
Each Windows configuration passed 290 tests; Linux ran the same suite with four
Windows-Job-specific tests explicitly skipped, while POSIX supervision and
cross-process tests passed. Frontend passed all 24 tests and the production build.
The first candidate run exposed a Linux test synchronization lifetime bug:
an inline Event was garbage-collected before a spawned child rebuilt its POSIX
semaphore. Keeping the parent reference until child completion fixed it. No OS,
Python version, test or acceptance criterion was removed. The failed run remains
[visible](https://github.com/Monisarek/sentinel-cg/actions/runs/42268950129).

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
