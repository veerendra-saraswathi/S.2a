# Dataset to Ontology bindings

This is the first bounded source-binding implementation on the v0.2 foundation. It materializes up to 1,000 rows from one committed dataset snapshot into one existing Object Type. JSON, Parquet and Iceberg use the same governed publication path. It is a local reference capability, not a distributed ingestion service.

## Contract

`bindings.define` publishes an immutable definition under `binding/<name>` in the branch manifest. It records the source dataset, source primary key, identity namespace, destination Object Type revision, source schema hash, field mappings, Action-owned properties and their initial defaults. Mappings are **target property → source field**. Scalar types must match exactly. Every target property has exactly one owner. Required Action-owned properties require initial values.

Definitions are versioned as immutable revisions and inherited by forks. Direct replacement is rejected. v0.3.2 supports [reviewed compatible binding migration](binding-migrations.md). Changing the Object Type revision or source schema requires explicit migration, which normal materialization rejects; use the reviewed migration path for supported changes. Names/references use 1–100 letters, digits, dots, dashes or underscores.

`bindings.materialize` consumes the dataset and binding selected by `expected_head`; no arbitrary historical snapshot parameter is accepted. Source identity is the canonical primary-key value plus binding namespace. Its SHA-256-derived object ID is stable within the project. A collision with a manually created object or another binding is rejected. Source primary keys must already satisfy the dataset contract.

All candidate objects are validated before a single branch publication. The branch head, object revisions, build replay record and success audit event commit in one SQLite transaction. A failure rolls them all back. New builds require the current expected head. Concurrent publications have one winner.

## Ownership and refresh

Source-owned properties can change only through materialization. Existing `objects.put` and Action writes reject changes to those fields and retain source provenance. Action-owned properties retain their current values during refresh; defaults apply only at first creation. The generic object API still supports direct authorized edits of non-source fields, as in the existing foundation. A domain that requires every operational edit to use approvals must additionally constrain that API; ownership alone does not impose an approval requirement.

Optional mapped source fields that disappear are removed from the new object. A missing required target field rejects the whole build. By default, a source row disappearing after a previous materialization rejects the whole build. v0.3.1 adds explicit administrator-reviewed retention; see [source reconciliation](source-reconciliation.md). No object is silently hard-deleted. Initial empty snapshots are allowed. Each new idempotency key denotes a new build; replay suppression applies to the same key and request, not arbitrary repeated inputs with different keys.

## Governance

The caller needs write permission and project membership, full source-schema/property access, all source-row access, target-schema access and destination-object access. Filtered source reads are never treated as full snapshots. Derived objects conservatively receive the combined source schema, all source-column, row, binding and destination restrictions, including existing object restrictions. This can hide an entire object even if only one source column is restricted; finer-grained propagation is future work.

Each derived object retains its source dataset, row and column policy resource keys. Reads and writes reapply current project policy floors to those keys, including on old branches. Hidden derived endpoints also hide their links. Build retries reauthenticate and revalidate current source and output access before returning the recorded result.

`bindings.provenance` returns the source dataset revision, binding revision, target type revision, source key, mapping and base commit for an authorized object. It requires access to the whole object and target schema. Mapping entries identify source-owned properties only. Later Action edits retain the last materialization provenance; their operation history remains in the existing audit/commit system. This is not a full lineage UI or per-property Action history service.

## API examples

All operations use the existing `Service.call` / `/v1/operations` envelope. Identifiers below are placeholders.

```json
{
  "operation": "bindings.define",
  "params": {
    "branch": "BRANCH_ID",
    "expected_head": "CURRENT_COMMIT",
    "binding": "equipment-assets",
    "dataset": "equipment",
    "object_type": "Asset",
    "namespace": "synthetic-lab",
    "mappings": {"asset_id": "asset_id", "reading": "reading"},
    "action_properties": ["status"],
    "initial_values": {"status": "pending"}
  }
}
```

After definition, obtain the new head and call:

```json
{
  "operation": "bindings.materialize",
  "params": {
    "branch": "BRANCH_ID",
    "expected_head": "POST_DEFINITION_COMMIT",
    "binding": "equipment-assets",
    "idempotency_key": "initial-build"
  }
}
```

For retry, reuse that exact expected head, binding, actor and key. Reusing a key for a different request is a conflict. Successful responses include status, commit, object IDs/revisions and the pinned dataset, type and binding revisions. Replays may append an audit event but do not publish another commit.

`bindings.provenance` parameters are `branch` and `object_id`. Non-derived objects return `source_binding: null` to authorized callers.

## Running the synthetic demonstration

```bash
.venv/bin/python tooling/bindings_demo.py --storage iceberg
```

The demo creates a disposable database, imports pump-01, defines a binding, materializes it, verifies replay, records a separately approved inspection Action, refreshes the source reading from 10 to 25, and confirms status remains `inspected`. It prints provenance and audit verification without credentials. It removes its temporary database and warehouse on exit. Substitute `json` or `parquet` to exercise those backends.

## Deployment and limitations

Stop old S2 processes before applying this update. Back up a persistent catalog and its warehouse together before using bindings on it. Creating the first binding transactionally advances the catalog format to **3**, so older executables reject the catalog on startup. Restarting preserves the current format for all storage backends; successful retained-absence reconciliation advances it to format 4. Rollback requires restoring a pre-binding catalog/warehouse backup, not simply running old code against the upgraded database.

Forks remain supported. The legacy merge endpoint rejects binding-bearing manifests; v0.3.3 provides the explicit reviewed merge path described below. This version supports reviewed compatible binding migration but has no automatic CDC, scheduling, transforms, link materialization, hard deletion, query indexes, external write-back or production scale claim. Retained-absence reconciliation is supported separately. Initial scope is synthetic engineering/maintenance records; RCI domain acceptance is separate.

## Reviewed scenario publication (v0.3.3)

Use [reviewed binding-aware merges](reviewed-merges.md) to publish coherent scenario changes. The legacy merge endpoint remains guarded.
