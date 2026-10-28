# Evidence remediation audit

Inspection started at `bcb0d9c4b5091775881ee1c209df0d4c449e7f31` on October 28, 2026. The two supplied texts contain the North Star objective and remediation instructions. They do not contain the independent report's F13–F18 findings; those findings cannot be reconstructed from their identifiers.

| Finding | Confirmed in inspected code? | Priority | Disposition |
| --- | --- | --- | --- |
| F1: re-attack completion can mask a bypass | YES | P0 | FIXED in `ecbef35`: actual fresh SQLite bypass yields overall FAIL and BYPASSED |
| F2: Red prompt and tool description reveal the mechanism | YES | P0 | FIX: freeze neutral adversarial evaluation prompts |
| F3: direct invariant shield comparison missing | YES | P0 | TEST: compare the same existing cases and utility workflows |
| F4: random baseline missing from committed source | YES | P0 | TEST: freeze seeds before measurement; keep all attempts |
| F5: same-topology robustness sweep | PARTIAL | P1 | DEFER until mandatory evidence gates pass; generator already exists |
| F6: Blue prompt prescribes a field-comparison strategy | YES | P0 | FIX: retain DSL and utility requirements without prescribing a condition |
| F7: Wonder reservation exceeds the example Blue cap | YES | P0 | FIXED: zero-network preflight; six tests pass; all spending limits retained |
| F8: transport retries absent | YES | P2 | DEFER: no observed transient live failure justifies retries |
| F9: Chat Completions lacks cross-turn reasoning continuity | UNVERIFIED in this workload | P2 | DISCLOSE: keep the current documented API until a reproduced blocker exists |
| F10: portable recorded evidence missing | YES at inspected HEAD | P1 | FIXED in `a5b6605`; clean-checkout acceptance still required |
| F11: dirty-tree competition export protection missing | YES | P1 | FIX: distinguish development from competition-grade exports |
| F12: final release remains draft and unproven | PARTIAL | P1 | DISCLOSE: preserve main; require actual release gates and submission assets |
| F13 | UNVERIFIED: report text missing | Unassigned | DISCLOSE |
| F14 | UNVERIFIED: report text missing | Unassigned | DISCLOSE |
| F15 | UNVERIFIED: report text missing | Unassigned | DISCLOSE |
| F16 | UNVERIFIED: report text missing | Unassigned | DISCLOSE |
| F17 | UNVERIFIED: report text missing | Unassigned | DISCLOSE |
| F18 | UNVERIFIED: report text missing | Unassigned | DISCLOSE |

Initial recorded checks: the accepted UI block passed 171 Python tests, 13 frontend tests, and the production frontend build. The first expanded 187-test run found one assertion-message mismatch in a new portability negative test; the test now reaches the intended aggregate-provenance check, and all 13 focused portability tests pass. No historical evidence or frozen fixture was changed to resolve it.

Official documentation reviewed: [model IDs and context windows](https://dev.meta.ai/docs/models), [Standard-mode prices](https://dev.meta.ai/docs/pricing-rate-limits), [Chat Completions](https://dev.meta.ai/docs/protocols/chat-completions), [tool calling](https://dev.meta.ai/docs/tool-calling), and [structured output](https://dev.meta.ai/docs/structured-output). Numeric maximum output ceilings were not established by these pages. Account entitlement, credits, and actual transport behavior remain unverified.

The participant-provided Official Rules specify Impact 30%, Use of Meta AI Tooling 25%, Technical Depth & Execution 20%, and Demo 25%. The [public organizer page](https://events.cerebralvalley.ai/e/meta-ai-developer-hackathon) separately confirms the Indie considerations of creativity, technical ambition, and actual functionality; its public page does not expose the numerical submission rubric. Personal eligibility information is outside this technical audit.
