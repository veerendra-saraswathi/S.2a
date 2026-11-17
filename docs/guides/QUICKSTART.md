# Quick start

[Repository](../../README.md) · [Documentation index](../README.md) · [API reference](../api-contracts/gate1.md)

## 1. Choose the repository

Run commands from your existing checkout:

```bash
cd ~/S.2a
git status --short
python3 --version
```

Python 3.11 or later is required. Initial storage-dependency installation needs package access. The source launcher does not require an editable installation of S2.

## 2. Install the tested storage dependencies

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-storage.lock
```

For a JSON-only demonstration, this installation can be omitted:

```bash
python3 tooling/s2.py --db .local/json-demo.db demo
```

Storage tests are explicitly skipped when their dependencies are absent. Use the environment flag below when validating the complete release.

## 3. Run all tests

```bash
S2_REQUIRE_STORAGE_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
python3 tooling/check_headers.py
```

The v0.6.0 baseline is **545 passing tests**, with no skipped file-storage suites. See the [pipeline validation record](../operations/orchestration-validation-2026-11-18.md).

## 4. Demonstrate Iceberg and verify artifacts

```bash
.venv/bin/python tooling/s2.py --db .local/iceberg-demo.db --storage iceberg demo
.venv/bin/python tooling/s2.py --db .local/iceberg-demo.db storage-report
```

Expected output fields:

| Field | Expected value |
| --- | --- |
| `action.status` | `COMMITTED` |
| `object.values.status` | `inspected` |
| `audit.valid` | `true` |
| `backend` in storage report | `iceberg` |
| `broken_references` | `[]` |
| `unreferenced_artifacts` after a clean demo | `[]` |

A fresh demo creates two referenced dataset artifacts. Rerunning the demo creates another synthetic tenant, so the total artifact count grows. The command does not issue external controller commands or print authentication credentials.

Use a separate database to try Parquet:

```bash
.venv/bin/python tooling/s2.py --db .local/parquet-demo.db --storage parquet demo
```

Backend and absolute warehouse path are persisted. Once configured, omit the flags on restart. File-backed installations reject an unplanned backend or warehouse switch.

## 5. Start the local development API

Use a separate development database:

```bash
.venv/bin/python tooling/s2.py --db .local/dev.db bootstrap --tenant "Development" --clearance 3 --compartment ENG
.venv/bin/python tooling/s2.py --db .local/dev.db serve --port 8080
```

Bootstrap prints the initial administrator credential once. Keep it private; do not commit it or paste it into shared issue reports. The service listens on `127.0.0.1` and exposes:

- `GET /health` for development liveness.
- `POST /v1/operations` with an `Authorization: Bearer ...` header.

Request envelope example:

```json
{
  "operation": "projects.create",
  "params": {"name": "Engineering pilot"}
}
```

See the [operation reference](../api-contracts/gate1.md) for parameters, authorization and retry contracts. The loopback server is a development adapter; production ingress is future work.

## 6. Existing data and recovery

Before opting an existing JSON database into file storage, stop server processes and back up the database. Original JSON snapshots remain readable, and a later committed mutation creates a file-backed revision. File enablement advances the database format to version 2, which the old v0.1 application refuses to open.

Keep the catalog and warehouse together in backups. Restore Iceberg artifacts at their original absolute location. `storage-report` identifies broken references and unreferenced artifacts but never deletes or repairs them. Follow the [storage recovery design](../architecture/file-storage.md).

## 7. Keep the checkout current

After a documentation update has been pushed and your local checkout is clean:

```bash
git pull --ff-only origin main
```

Use `git --no-pager diff --cached --stat` when reviewing staged changes without entering Git's interactive pager.

## 8. Demonstrate Ontology bindings

```bash
.venv/bin/python tooling/bindings_demo.py --storage iceberg
```

This uses a disposable database. Expected: `replay_same_result: true`, `approved_status_preserved: true`, reading 25, status `inspected`, and `audit.valid: true`. See [contracts and format-3 upgrade guidance](../architecture/ontology-bindings.md).

For retained absence and source return, run `.venv/bin/python tooling/bindings_demo.py --storage iceberg --reconcile`. Expected final reading: 30, status: inspected, presence: PRESENT, with MISSING recorded earlier in the demonstration.

For the combined migration demo, add `--migrate` to the command above. It adds reading_copy and review_note, preserves inspected status and verifies replay. Successful migration uses catalog format 5; see [compatibility guidance](../architecture/binding-migrations.md).

## File-ingestion demonstration

```bash
.venv/bin/python tooling/ingestion_demo.py --storage iceberg
```

Expected: two materialized objects, replay equality, valid audit and no broken storage references. Real-file commands are documented in [file ingestion](../architecture/file-ingestion.md).

## Pipeline demo

```bash
.venv/bin/python tooling/pipeline_demo.py --storage iceberg
```

Expected: two input rows, one output row, one materialized object, valid audit and replay equality. Pipeline definitions advance the catalog to format 6; old executables refuse it. Use the reviewed merge API for pipeline-bearing data branches; the legacy endpoint remains blocked. See [contract](../architecture/versioned-pipelines.md).

## Reviewed pipeline scenario demo

```bash
.venv/bin/python tooling/pipeline_demo.py --storage iceberg --merge
```

Expected: same object ID, reading 40, inspection preserved, source unchanged, replay equality and valid audit.

## Pipeline-chain demo

```bash
.venv/bin/python tooling/orchestration_demo.py --storage iceberg
```

Expected: initial quality rejection, then order `z-select`, `a-validate`, one object, matching replay and valid audit.
