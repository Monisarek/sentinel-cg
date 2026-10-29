# Verify a portable Evidence Bundle

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

The exporter rejects unresolved source commits, changing files, SQLite sidecars,
credential fields, and symlinks. The package intentionally excludes the persistent
spending ledger: replaying recorded evidence never authorizes another model call.
