# SENTINEL-CG 2.0

**Evidence-driven safety testing for autonomous agents.**

Both actions targeted staging. Production still fell below its safety floor.
In this local SQLite experiment, two permitted staging actions compete for a
shared connection pool and reduce production capacity from 8 to 6. SENTINEL-CG
verifies a narrow policy repair, checks that legitimate work still succeeds, and
shows exactly where replay under the two policies diverges.

The Evidence Workspace runs without an API key or paid service. The action trace
and policy candidate are explicitly labeled handcrafted fixtures. The Gateway
decisions, state changes, oracle verdicts, and replay are executed locally.

[Portfolio case study](docs/PORTFOLIO.md) ·
[Architecture and trust boundaries](docs/PORTFOLIO.md#architecture-and-trust-boundaries) ·
[Verify the evidence](docs/VERIFY_EVIDENCE.md)

![Evidence Workspace showing production at 6 under v0 and 8 under verified v1](docs/images/evidence-workspace.png)

## Run the demo

On Windows, install Git, Python 3.10+ and Node.js 22.12+ with npm. Run from a
PowerShell terminal:

```powershell
git clone --branch build https://github.com/Monisarek/sentinel-cg.git
cd sentinel-cg
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-demo.ps1
```

Open <http://127.0.0.1:8000>. The launcher prepares `.venv`, installs free
dependencies from the Python and npm registries when needed, builds the frontend,
and serves it on loopback. Stop it with Ctrl+C. If the default port is busy,
append `-Port 8001` and open <http://127.0.0.1:8001>. Registry access is needed
for an uncached first install. The experiment itself runs offline.

On Linux or macOS, use the same prerequisites and run from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
(cd apps/web && npm ci --ignore-scripts && npm run build)
python -m scripts.serve_evidence --output-dir .cache/portfolio
```

1. Select **Run deterministic proof**, then **Initial state** to inspect the shared
   snapshot. Advance through the staging configuration change and restart.
2. At the restart, compare v0's ALLOW and production allocation of 6 with v1's
   DENY and preserved allocation of 8. Inspect the committed deltas and the
   production minimum supplied by the trusted task contract.
3. Review the deny-only patch, the rejected overblocking control, and the separate
   safe staging repair. The repair restores staging to 4 while production stays at 8.
4. Select **Download JSON** for the run record. Repeat the proof to create an
   independent run. For a portable package with SQLite snapshots and source, use
   the commands below.

## Engineering decisions

- **Preview and commit use the same deterministic transition.** The Gateway
  validates typed actions against trusted task scope and policy. Preview and
  atomic commit are tied to the current SQLite version. A DENY leaves the
  sandbox unchanged.
- **Safety and utility are checked independently.** The oracles inspect
  committed state and the trusted task contract. A candidate that blocks the
  counterexample and also blocks required workflows fails verification.
- **Promotion is controlled.** A restricted, deny-only policy patch must pass the
  original counterexample, frozen regression and held-out checks before the
  registry appends immutable v1. The proposal source cannot promote a patch.
- **Replay isolates the policy variable.** The Safety Time Machine starts two
  independent SQLite forks from the same state and executes the same proposals
  under v0 and v1, recording the first divergence and persisted final states.

## Verify the evidence

Use the Run ID from **Evidence provenance and integrity**. The demo commands
above write runs under `.cache/portfolio/`. Substitute the actual Run ID for
`RUN_ID` and use an unused output directory. Run both commands at the repository
root, using Python 3.10+:

```text
python -B -m scripts.export_evidence .cache/portfolio/RUN_ID/evidence.json .cache/portable-demo
python -S -B -m scripts.verify_evidence .cache/portable-demo
```

On Windows the launcher interpreter is `.\.venv\Scripts\python.exe`. The
independent verifier uses the standard library and Git and opens the SQLite
snapshots read-only. The original run status and fixture provenance stay visible
in its verdict. To verify elsewhere, check out the pinned verifier commit from
the bundled Git source. [Verification instructions](docs/VERIFY_EVIDENCE.md)

The recorded evidence collection includes original JSON, portable packages and
retained baseline attempts. Read the source commit and working-tree status before
comparing measurements. [Evidence archive](evals/evidence/remediation-offline-evidence.zip) ·
[SHA-256](evals/evidence/remediation-offline-evidence.zip.sha256) ·
[Recorded summary](evals/evidence/remediation-summary.json)

## Checks and measurements

Install test dependencies with `python -m pip install -r requirements-dev.txt`
inside the environment you created above.

```text
python -m unittest discover -s tests -v
npm --prefix apps/web test
npm --prefix apps/web run build
python -m scripts.eval_random
python -m scripts.eval_shield
python -m scripts.eval_parametric
```

The clean-checkout acceptance run at `913231e` passed 222 Python tests,
18 frontend tests, the TypeScript check, and the production build. Baseline
reruns reproduced 84/100 counterexamples with the seeded Random Explorer.
The Windows launcher and Chrome walkthrough passed first load, repeated runs,
JSON download, error recovery, and desktop widths of 1280, 1440, and 1920 pixels,
with zero page errors or external requests during the walkthrough.
The Direct Invariant Shield and fixture v1 each blocked 5/5 unsafe
cases and preserved 20/20 required workflows. The parameter sweep blocked 100/100
unsafe cases and preserved 500/500 required workflows across the same topology.
Run identifiers, source provenance, denominators, and limitations are in the
[evaluation report](docs/EVALUATION.md). [Remediation audit](docs/AUDIT.md)

## Scope and provenance

SENTINEL-CG is a bounded engineering prototype for a synthetic shared-capacity
system. It exercises one v0-to-v1 policy repair cycle and evaluates declared
invariants under deterministic transitions. Deployment safety on real
infrastructure, adaptive discovery, and generalization to other topologies
remain untested.

The project began at the Meta Global AI Developer Hackathon 2026. The optional
Meta Model API adapter and frozen live evaluation protocol remain in the
repository. Genuine inference remains untested, and competition gates G3, AI-dependent
G4/G7, and G8 remain PENDING. The historical `offline-core-v0.1` tag identifies
the earlier offline core. [Engineering specification](docs/ENGINEERING_SPEC.md) ·
[Frozen live protocol](evals/protocol/live-v1.json)

MIT licensed. [License](LICENSE)
