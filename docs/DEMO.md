# Deterministic Evidence Walkthrough

This is the existing three-minute scenario using a `deterministic_test_fixture` while Meta access is pending. Both the counterexample and candidate are handcrafted. Every displayed state comes from actual SQLite execution. Do not describe this recording as autonomous discovery, an AI-generated repair, or a live Meta run.

Start `python -m scripts.serve_evidence` after building `apps/web`. Open the workspace and select **Run deterministic proof**. Keep the fixture banner visible.

| Time | Evidence to show | Explanation |
| --- | --- | --- |
| 0:00–0:15 | Final v0 production allocation 6, protected minimum 8; staging allocation 6 | Permitted staging actions caused an indirect production failure in deterministic sandbox execution. |
| 0:15–0:45 | Select Initial state, then actions 1 and 2 | Config changes pending staging demand. Restart activates demand and reallocates shared capacity. Show the actual production allocation delta 8 → 6. |
| 0:45–1:15 | Recorded action trace and fixture provenance | The trace is handcrafted for this offline proof. Meta discovery is PENDING. No model selected these actions in this run. |
| 1:15–1:45 | Candidate conditions and independent verification | The handcrafted deny-only patch compares projected allocation with the protected minimum. The Promotion Gate verifies safety, legitimate utility, frozen regression, and held-out cases. The deny-all-restarts negative control is rejected. |
| 1:45–2:15 | Policy v0/v1, identical initial hash and trace, first divergence at action 2 | Both forks execute in independent SQLite databases. v0 allows restart and reaches production 6. v1 denies restart and preserves production 8 with zero mutation on DENY. |
| 2:15–2:40 | Separate safe staging repair after promotion | Under the active verified v1, staging reaches 4 and production remains 8. Blocking the counterexample does not complete the repair by itself; the safe workflow is executed separately. |
| 2:40–3:00 | Provenance, run ID, Git commit, JSON download, PENDING labels | Export the actual Evidence Bundle. G3, AI-generated G4 repair, and adaptive G7 re-attack require new genuine permitted Meta calls. |

The final competition recording must replace the discovery and synthesis sections with genuine Meta evidence and include a fresh adaptive re-attack. Until then, retain the fixture labels and pending claims. No competition-ready PASS is implied by this walkthrough.
