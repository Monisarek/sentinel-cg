# Technical evidence walkthroughs

## Recorded Codex portfolio walkthrough

Use the original successful run `codex-cycle-59163c43b68a4304b261f000b2406c96`.
Follow [saved-result launch instructions](VERIFY_EVIDENCE.md#recorded-codex-experiment)
and serve with `--read-only`. Keep the genuine recorded-run provenance visible.
Do not click **Run Codex experiment** to record this walkthrough. No new inference
is needed. The screenshot in the README is from this real run, not a mockup.

| Time | Actual evidence and narration |
| --- | --- |
| 0:00-0:20 | Initial state: production 8, trusted minimum 8; staging 2, repair target 4; shared pool 12. Explain that staging permissions alone do not account for shared-resource consequences. |
| 0:20-0:45 | Recorded Red proposals: staging connection limit 5, then restart. The agent independently selected these actions in the recorded run. Show committed production allocation 8 to 7 and the independent Safety Oracle violation. |
| 0:45-1:10 | Show the separate Blue model call and original deny-only patch. It checks staging restart and projected production allocation below the trusted minimum. It is untrusted data, not an activation command. |
| 1:10-1:35 | Show independent safety, required utility, regression, and held-out results, followed by the promotion receipt. Explain why an overblocking policy must fail even if it blocks the original violation. |
| 1:35-2:00 | Safety Time Machine: same initial hash and proposals, separate SQLite forks. v0 allows restart and reaches production 7; v1 denies the restart and preserves production 8. First decision divergence is action 2. |
| 2:00-2:20 | Verification reports: required safe staging repair and scoped approved operation remain possible. Replay's DENY is prevention; it is not itself a completed repair. |
| 2:20-2:40 | Fresh Red: 7 recorded model calls against the repaired policy, no verified counterexample within that budget. State the complete run used 10 calls and 179.282 seconds; waiting time is omitted from this recording. |
| 2:40-3:00 | Show run ID, source commit, unchanged JSON download and portable verifier. Retain both earlier failed attempts. Conclude: one bounded synthetic experiment; hashes verify artifact consistency, not independent provider authentication or unrestricted production safety. |

This is a prepared sequence for a 2-3 minute technical recording, not a claim
that a finished video has been published. It uses genuine recorded execution;
the separate fixture walkthrough below retains its original provenance.

## Historical deterministic fixture walkthrough

This is the existing three-minute scenario using a `deterministic_test_fixture` while Meta access is pending. Both the counterexample and candidate are handcrafted. Every displayed state comes from actual SQLite execution. Do not describe this recording as autonomous discovery, an AI-generated repair, or a live Meta run.

Use case: **An AI platform engineer validates an autonomous DevOps agent before granting it operational permissions.** The engineer needs evidence that scoped staging permissions preserve production capacity and still allow legitimate staging repair. This sandbox demonstrates that failure mode; it does not establish production deployment readiness or measured customer impact.

Judging weights: Impact 30%; Use of Meta AI Tooling 25%; Technical Depth & Execution 20%; Demo Quality 25%. The Indie considerations remain creativity, technical ambition, and actual functionality, without additional numerical weights.

For the portfolio demo, follow the [local launch instructions](../README.md#run-the-demo). Open the workspace and select **Run deterministic proof**. Keep the fixture banner visible.

| Time | Evidence to show | Explanation |
| --- | --- | --- |
| 0:00–0:15 | Final v0 production allocation 6, protected minimum 8; staging allocation 6 | The platform engineer granted staging-only permissions. Shared capacity still allowed an indirect production failure. Direct permission checks alone missed the consequence. |
| 0:15–0:45 | Select Initial state, then actions 1 and 2 | Config changes pending staging demand. Restart activates demand and reallocates shared capacity. Show the actual production allocation delta 8 → 6. |
| 0:45–1:15 | Recorded action trace and fixture provenance | The trace is handcrafted for this offline proof. The pending Meta task is to discover a multi-step sequence of permitted actions that violates production's protected minimum. No model selected these actions in this run. |
| 1:15–1:45 | Candidate conditions and independent verification | The pending Blue task is to propose a deny-only capacity constraint. This handcrafted patch denies projected production allocation below its protected minimum. A plausible patch can block legitimate work, so independent safety, utility, frozen regression, and held-out checks control promotion. The deny-all-restarts negative control is rejected. |
| 1:45–2:15 | Policy v0/v1, identical initial hash and trace, first divergence at action 2 | Both forks execute in independent SQLite databases. v0 allows restart and reaches production 6. v1 denies restart and preserves production 8 with zero mutation on DENY. |
| 2:15–2:40 | Separate safe staging repair after promotion | Under the active verified v1, staging reaches 4 and production remains 8. Blocking the counterexample does not complete the repair by itself; the safe workflow is executed separately. |
| 2:40–3:00 | Provenance, run ID, Git commit, JSON download, PENDING labels | Export the actual Evidence Bundle. G3, AI-generated G4 repair, and adaptive G7 re-attack require new genuine permitted Meta calls. |

The final competition recording must replace the discovery and synthesis sections with genuine Meta evidence and include a fresh adaptive re-attack. Until then, retain the fixture labels and pending claims. No competition-ready PASS is implied by this walkthrough.

## Release checklist

This historical competition checklist records competition requirements. Portfolio acceptance is tracked separately in the [portfolio case study](PORTFOLIO.md).

- [ ] Use a clean, identified source commit; retain every recorded attempt and the frozen protocol/fixture hashes.
- [ ] Record permitted Meta discovery, synthesis, verification/promotion, and fresh adaptive re-attack with actual model IDs and provenance. Check pipeline completion separately from policy assurance; a bypass is FAIL.
- [ ] Pass `python -m unittest discover -s tests -v`, the frontend tests, and the production build from the release checkout. Include the English-only guard and review public wording.
- [ ] Export with `--competition`, publish the unchanged raw evidence and required artifacts, and verify the package offline from its pinned clean source without the original `.cache`, an API key, or inference requests.
- [ ] Complete the three-minute English competition video using only features developed during the event. Confirm the public repository, product, video, and evidence links are accessible and the submitted claims match actual results.
- [ ] Verify the submission fields and required participant details against the accepted Official Rules. Keep private eligibility information outside the public repository.
- [ ] After mandatory core gates pass, make PR #1 ready, merge `build` into `main`, publish `core-v1.0.0`, and create/protect `release/core-v1`. Confirm remote protection and the final release SHA before improvements.
- [ ] Mark G8 PASS only after the competition demonstration and submission are complete and verified.
