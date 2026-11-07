# Verify a portable Evidence Bundle

## Recorded Codex experiment

The [Codex collection](../evals/evidence/codex-subscription-evidence.zip) includes
the completed model-driven cycle and both earlier unsuccessful attempts.
Check its [SHA-256](../evals/evidence/codex-subscription-evidence.zip.sha256).
The [summary](../evals/evidence/codex-subscription-summary.json) records each
attempt's source, outcome, call count, and manifest hash. Each package retains
its original JSON, reports, policy registry, and SQLite snapshots.
Codex provenance identifies CLI sessions. In these recorded attempts the legacy
`response_id` field aliases the same `session_id`; the workspace labels it as a
session identifier.

To view the saved result without calling a model, extract the collection from
the repository root and use the existing built workspace:

```powershell
Expand-Archive .\evals\evidence\codex-subscription-evidence.zip .\.cache\codex-recorded
.\.venv\Scripts\python.exe -m scripts.serve_evidence --read-only --output-dir .cache/codex-recorded/codex-subscription-proof/recorded-runs
```

Open <http://127.0.0.1:8000/?run_id=codex-cycle-59163c43b68a4304b261f000b2406c96>.
Use an unused extraction folder. Starting a new experiment with the Codex button
consumes subscription usage. Opening the recorded result and downloading its
original JSON use saved local data.

The collection's `JUDGE.md` gives exact independent verification commands.
Clone its `verified-cycle/source.bundle`, select verifier commit
`901ce3bb0957769270ab530993436454cf616398`, and run the commands below for
each package. All three packages passed a separate Python `-S -B` verification
with network, writes, and original-cache reads forbidden.

## Package verification

This package contains the original, unchanged JSON Evidence Bundle, recorded
traces and verification reports, policy records, final SQLite snapshots, and a
self-contained Git source bundle. It does not contain API keys, authorization
files, spending ledgers, dependencies, or billing credentials.

1. Compare `manifest.json` SHA-256 with the value supplied by the publisher through
   a trusted channel. File hashes establish integrity, not authenticity. Review
   `source_commit`, `verifier_commit`, provenance, and `source_working_tree_dirty`.
   A dirty source run is disclosed; its uncommitted changes are not in Git.
   Default exports have `export_grade=development`. `--competition` rejects a
   dirty source run or exporter checkout. This grade concerns source provenance;
   it never changes fixture labels or acceptance gates.
2. Explicitly clone the source bundle and check out the trusted verifier commit:

   ```text
   git clone /path/to/package/source.bundle sentinel-verifier
   cd sentinel-verifier
   git checkout <verifier_commit from manifest.json>
   python -B -m scripts.verify_evidence /path/to/package
   ```

   Python 3.10 or later and Git are sufficient. No installation, original
   `.cache`, API key, inference payment, or network access is needed. The command
   reads package files and SQLite in read-only mode; `-B` disables bytecode writes.
   Cloning and checkout are explicit preparation, outside the read-only command.
3. Read the `VERIFIED` result alongside `original_status`, `runtime_status`, and
   the unchanged acceptance gate statuses. A valid partial run remains partial.
   Fixture evidence remains `deterministic_test_fixture`; verification cannot
   establish genuine Meta inference or turn pending AI gates into PASS.

The verifier checks the allowlisted file set and SHA-256 manifest; bundle, event,
replay, candidate and policy hashes; promotion receipts and frozen evaluation
fingerprints; recorded actions against the existing pure transition and preview;
actual deltas and independently recomputed safety/utility results; and final
SQLite snapshots. Recorded event history is checked for consistency, rather than
independently proving every historical database write. Historical absolute path
fields inside the original JSON are inert metadata and are never followed.

To export a completed or stopped run from a trusted checkout:

```text
python -B -m scripts.export_evidence /path/to/run/evidence.json /path/to/new-package
```

For a competition-grade export, use a clean checkout and append `--competition`.
Missing genuine Meta stages still block the final competition release, even if
an individual fixture artifact has clean source provenance.

The [offline portfolio launcher](../README.md#run-the-demo) writes fresh runs to
`.cache/portfolio/<run_id>/`. New runs created in the workspace use run-relative
artifact names. The browser downloads the JSON record. The CLI exporter adds the
original reports, SQLite snapshots, and Git source for independent verification.

The exporter rejects unresolved source commits, changing files, SQLite sidecars,
credential fields, and symlinks. The package intentionally excludes the persistent
spending ledger: replaying recorded evidence never authorizes another model call.

## Resource, privacy, and retention boundaries

The current exporter and verifier enforce fixed limits before parsing, hashing,
or opening an artifact: 16 MiB per JSON/instructions file, 64 MiB per artifact,
256 MiB per package, 1,024 files, 4,096 directory entries, and eight directory
levels. SQLite inspection is limited to 10,000 rows per allowlisted table.
Hashes use bounded streaming reads. Git checks have a 60-second timeout.
The verifier rejects symlinks/reparse points, unexpected SQLite tables, binary
SQLite payloads, excessive structured-data nesting, and oversized inputs. These
are defensive local-tool limits, not a guarantee against arbitrary hostile Git
or SQLite parser defects. Verify with an updated, trusted local toolchain.

Every selected JSON report, SQLite text/schema field, and copied instruction
file is checked for credential fields, bearer credentials, known key patterns,
and private-key markers. A detected credential aborts export before artifacts
are copied. New manifests contain a recomputed `privacy_review` with counts of
absolute local paths and email-like values; it never echoes those values.
Original bytes are preserved, so a nonzero count requires human publication
review. Historical path strings remain inert metadata and can reveal a former
local username even though verification never follows them.

Pattern checks cannot prove the absence of secrets or personal information in
arbitrary prose, images, encrypted content, or Git history. `source.bundle`
includes reachable source history and requires separate human review. The
manifest explicitly records `source_history_review=REQUIRED`; a clean working
tree or `NO_KNOWN_PATTERNS_FOUND` result is not a privacy certification. Do not
publish a sensitive package. Preserve the original privately and prepare a
separately identified public artifact with owner approval if redaction is needed.

Retention is explicit: published evidence is retained indefinitely, and local
unpublished artifacts require operator review after 30 days. Export and verify
never delete runs, failures, source history, or existing packages. Each export
creates only the requested new directory within the package limits. Review local
disk usage and remove unnecessary unpublished copies deliberately; do not delete
an active run or rewrite a published artifact. No secure-erasure claim is made.
The manifest records this policy, and the verifier remains read-only.

## Recorded clean-checkout proof

Download [the offline evidence collection](../evals/evidence/remediation-offline-evidence.zip)
and its [SHA-256](../evals/evidence/remediation-offline-evidence.zip.sha256).
The archive contains 749 files: four portable runtime packages, all recorded
random/shield/parameter-sweep attempts and SQLite snapshots, and verification
receipts. [The summary](../evals/evidence/remediation-summary.json) records hashes
and measurements; the archive's top-level `JUDGE.md` gives exact relative commands.

The source and verifier commit is
`4446dd3d4e84f2cc0de865421a34bdcb76527f7b`. A clean producer checkout exported
core, full runtime fixture, missing-key/budget refusal, and a real SQLite fixture
bypass. Another checkout was cloned only from the exported `source.bundle`.
Python 3.10.11 verified all four packages with `-S -B`, no installed dependencies,
no API key, network and original-cache reads forbidden, and a read-only open
guard. All package and verifier-checkout file hashes were unchanged.

The [UI check](../evals/evidence/remediation-ui-check.json) rendered these same
four original JSON files through the existing normalizer and React components.
The partial run had no inferred replay; the bypass remained FAIL/BYPASSED; all
AI gates remained PENDING. This check uses server rendering, not an interactive
browser session. The raw JSON was unchanged.

The archive and manifests establish recorded consistency and integrity, not live
model provenance, production fidelity, or competition readiness. Baseline report
formats are inventoried by the collection manifest; the per-package verifier
checks the four runtime proof formats. Original absolute path strings in raw JSON
remain inert metadata. No path from the original machine is required.
