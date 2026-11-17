# Explicit source absence reconciliation

S2 v0.3.1 adds retained-absence reconciliation to the v0.3.0 binding foundation. Source absence describes a committed dataset snapshot. It does not establish physical retirement, destruction, maintenance completion or authoritative deletion of an asset.

## Review then retain

1. Commit the source dataset change.
2. Call `bindings.preview` with `branch`, current `expected_head` and `binding`. It checks complete source access, target schema access and every owned object's current restrictions. It returns `missing`, an exact map of missing object IDs to revisions, plus the snapshot revision and commit. It does not change objects or guarantee that all later schema/value validation will pass.
3. An administrator calls `bindings.materialize` with that same head, `missing_policy: "retain"`, the exact `expected_missing` map, an idempotency key and a nonblank `reason` (at most 1,000 characters).
4. Publication atomically updates present source objects and retains missing objects with `source_presence: "MISSING"`. Values, links, approved operational edits and last source-binding provenance remain. An audit failure or any validation failure rolls back the whole publication.

The default `missing_policy` is `reject`. No deletion is implicitly acknowledged. A subsequent build while rows remain absent also requires a fresh explicit retention decision; there is no permanent auto-retain switch. A changed branch head, missing set or object revision invalidates the reviewed request. This is an explicit administrator decision, not a multi-party approval queue.

```json
{
  "operation": "bindings.preview",
  "params": {"branch": "BRANCH_ID", "expected_head": "CURRENT_COMMIT", "binding": "equipment-assets"}
}
```

Copy the returned `missing` map exactly into `expected_missing`:

```json
{
  "operation": "bindings.materialize",
  "params": {
    "branch": "BRANCH_ID",
    "expected_head": "PREVIEW_COMMIT",
    "binding": "equipment-assets",
    "idempotency_key": "reviewed-absence-001",
    "missing_policy": "retain",
    "expected_missing": {"OBJECT_ID": "OBJECT_REVISION"},
    "reason": "Retain engineering evidence while the missing source record is investigated"
  }
}
```

Retries must reuse the same actor, head, binding, policy, revision map, reason and key. Current permission checks still apply. Different decisions under the same key conflict; successful replay does not publish a second commit.

## Evidence and return of a source row

`objects.get` and `objects.list` expose `source_presence` for objects that have reconciliation history. `MISSING` means the retained source values are historical. `PRESENT` means the source row was observed again at the last materialization. An object without this field has no recorded reconciliation lifecycle; do not infer a live upstream state from its omission. An ordinary dataset update does not immediately update derived presence; a binding build must run.

`bindings.provenance` additionally returns `source_state`. It separates:

- `source_binding.dataset_revision`: the snapshot that supplied the retained values;
- `source_state.observed_dataset_revision`: the snapshot used for the latest presence decision;
- `last_reconciliation`: administrator, reason, prior object revision, reviewed base commit and the snapshot establishing absence.

If the same source identity returns, an ordinary successful materialization restores the same object ID to `PRESENT`, refreshes source-owned values, preserves operational fields and retains the last reconciliation evidence. Earlier revisions remain in commit history. This is observed source return, not approval to resume physical operation.

Generic object/Action edits preserve source state and cannot directly change it. Such edits remain permitted on operational fields of missing objects: for example, a reviewer can annotate the retained record. Domain-specific restrictions on those Actions remain a separate configuration concern. Retained objects and links remain queryable under policy; consumers must display source presence rather than present old readings as current.

## Security and limits

Complete snapshot visibility is required; filtered rows cannot be interpreted as deletion. Missing objects retain current source-policy dependencies and destination restrictions. Old branches remain subject to current restrictions. Provenance access requires whole-object and target-schema access; ordinary object responses expose only the presence label, not the administrator's reason.

There is no hard delete, purge, automatic retirement, binding migration, or binding-aware branch merge. Forks and branch-local reconciliation are supported. At most 1,000 source rows, 1,000 owned objects and 1,000 resulting objects are supported by this reference operation. Reconciliation and physical lifecycle semantics need customer agreement for production RCI use.

## Catalog compatibility

The first successful reconciliation upgrades catalog format 3 to **4** atomically with the objects and audit. Earlier binaries refuse that format at startup. Stop older running services before upgrading and back up persistent catalogs with their warehouses. Code rollback requires restoration of a pre-reconciliation backup. Failed reconciliation does not upgrade the catalog. Creating additional bindings and restarting with file storage preserve format 4.

## Demonstration

```bash
.venv/bin/python tooling/bindings_demo.py --storage iceberg --reconcile
```

The disposable demo approves an inspection, removes the source row, retains the object as `MISSING`, retries that decision, reintroduces the source row and restores `PRESENT`. Expected final reading: 30; status: inspected; same object identity; replay true; audit valid. No external control or write-back occurs.
