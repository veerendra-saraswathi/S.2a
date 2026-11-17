# Reviewed compatible binding migrations

S2 v0.3.2 adds `bindings.migration_preview` and `bindings.migrate` for the local reference runtime. A migration adopts the current compatible Object Type revision, updates explicit source mappings and adds fields while preserving object identity, existing field ownership, operational edits, source-presence history and classification restrictions.

This is a bounded binding migration service. It does not create or alter source dataset schemas, rename/remove Object Types or implement arbitrary transformation scripts. Object Type evolution uses the existing `ontology.define` contract. Optional additions can be published there first; existing object validation prevents adding a required property unless every existing object already satisfies it.

## Supported changes

| Change | Behavior |
| --- | --- |
| Adopt compatible current Object Type | Pin its current revision and validate all candidate objects |
| Remap an existing source-owned property | Allowed only to an existing source field of identical scalar type; included in reviewed effects |
| Add a source-owned target property | Add mapping to an existing source field; missing required values fail the whole preview/build |
| Add an Action-owned property | Declare its ownership and optional initial default; required properties need defaults |
| Existing operational values | Preserved exactly, including approved inspection status |
| Defaults for existing Action-owned fields | Frozen; defaults cannot overwrite or refill existing operational fields |
| Existing source/Action ownership | Cannot be transferred or removed |
| Identity | Dataset, namespace, primary key, object type and existing primary-key property mappings remain fixed |
| Missing source objects | Migration rejected, including retained missing objects; separate missing-object migration semantics remain open |
| Branches | Forks isolate migrations; binding-aware merges remain unsupported |

New Action-field defaults initialize that new field on existing objects during migration and initialize future objects. Later ordinary refresh preserves the field's current operational value; it does not repeatedly reset it to the default.

## Preview contract

Both operations require administrator role, project/tenant authorization, complete source access and access to all affected object and target-schema restrictions. The preview does not write revisions, objects, schema-format changes or commits. The normal request audit event is still appended.

Pass `branch`, `expected_head`, `binding`, the complete `mappings` dictionary (target property to source field), complete `action_properties` list, complete `initial_values` dictionary and a nonblank `reason` of at most 1,000 characters. Together, mappings and Action properties must cover the current target schema exactly once.

```json
{
  "operation": "bindings.migration_preview",
  "params": {
    "branch": "BRANCH_ID",
    "expected_head": "CURRENT_COMMIT",
    "binding": "equipment-assets",
    "mappings": {"asset_id": "asset_id", "reading": "reading", "reading_copy": "reading"},
    "action_properties": ["status", "review_note"],
    "initial_values": {"status": "pending", "review_note": "unreviewed"},
    "reason": "Adopt additional engineering fields while preserving approved inspection"
  }
}
```

The example assumes `reading_copy` and `review_note` have already been added to the target schema as compatible fields. The preview returns the prior and proposed binding revisions, source and target revisions, each candidate object's previous/proposed revision, the submitted mapping/defaults and a `plan_hash`. A previously unmaterialized valid source row may create a new object as part of the plan; such an entry has a null previous revision. Review those effects before applying.

The hash covers the pinned branch state, proposed binding, source snapshot and candidate object revisions, including effective restrictions. It is a checked reference to reviewed effects, not a digital signature or multi-party approval record. The migration records its administrator and reason; no second approver is required by this slice.

## Atomic application and replay

Call `bindings.migrate` with the exact same preview parameters, plus `plan_hash` and a fresh `idempotency_key`. The service recomputes the plan under current policy. A changed head, schema, source snapshot, object state, request or effective effect causes a conflict. An access revocation denies execution. Policy changes can invalidate a plan even without moving the branch head.

The new binding revision, all candidate object revisions, format upgrade, replay record, branch head and successful audit event commit in one transaction with **one new branch commit**. A failed object write, validation or audit rolls back the complete operation. Earlier binding/object revisions remain available through commit history.

Retries use the exact same actor, base, parameters, hash and key. A matching successful retry returns the recorded result after current access checks; it does not publish another commit. Changing a reason or payload under the same key conflicts. A fresh request after a concurrent publication requires a new preview.

## Provenance and security

`bindings.provenance` returns `binding_migration` for objects last built using a migrated binding. It includes the prior binding revision, migration base commit, actor and reason. `source_binding` records the new binding revision and actual source field mappings. Existing reconciliation history remains in `source_state`; prior migration history is retained in immutable binding revisions and commits.

Migration joins old binding restrictions, current source restrictions, current target restrictions and each old object's restrictions. It does not declassify data. Source policy dependencies continue to apply on old branches and subsequent reads. Provenance still requires whole-object and target-schema access.

## Format compatibility and limits

A successful migration advances the catalog to **format 5**. Preview or failed migration leaves its format unchanged. Binding creation, normal materialization, source reconciliation and restart preserve format 5 rather than downgrading it. Older binaries reject format 5 at startup; stop existing old processes before upgrade and keep a pre-migration catalog/warehouse backup for downgrade recovery.

The existing 1,000-row/owned-object/result-object bounds apply. There is no distributed schema rollout, arbitrary coercion, source schema editor, property removal, ownership transfer, identity reassignment, hard deletion, migration of missing objects or binding-aware merge implementation. Missing objects must genuinely return before this slice can migrate them, or await a future explicit migration policy; fabricating source rows is not a recovery procedure.

## Disposable end-to-end demonstration

```bash
.venv/bin/python tooling/bindings_demo.py --storage iceberg --reconcile --migrate
```

Expected: same object ID; source presence PRESENT after retained absence; approved status inspected; reading and reading_copy both 30; review_note unreviewed; migration replay true; binding revision changed; valid audit chain. The demo uses temporary data and performs no external writes.
