# Session checkpoint — 17 November 2026

## Delivered

S2 v0.3.0 adds a bounded dataset-to-Ontology materializer on source baseline `1b71d1123452baff6858818b35253ec9cbcf0698`. It exposes `bindings.define`, `bindings.materialize` and `bindings.provenance` through the existing service/API registry.

Twenty new scenarios run on each of JSON, Parquet and Iceberg: deterministic materialization/provenance/restart replay, approved-edit preservation, source ownership, invalid-row atomicity, storage failure, audit failure, branch isolation, explicit deletion conflicts, stale inputs, schema change rejection, restricted rows, current source policy on old branches, inherited column restrictions, retry authorization, identity collisions, tenant/membership/role isolation, binding validation, concurrent publishers, hidden links and the 1,000-row bound.

## Validation in the development workspace

- Full suite with `S2_REQUIRE_STORAGE_TESTS=1`: **164 tests passed**, no skips, 52.405 seconds.
- Original 104 tests remain in the suite; 60 binding tests were added.
- Proprietary header check: **26 files verified**.
- `git diff --check`: passed before packaging.
- Synthetic Iceberg binding demo: reading updated to 25; separately approved `inspected` status preserved; retry returned the same result; audit valid with 31 events; no external effects.

These are local functional results, not a GitHub CI run or RCI acceptance. Upstream deprecation warnings and sandbox CPU-cache-probe warnings were non-fatal. No dependency upgrade was made.

## Important boundaries

Creating a binding upgrades the catalog to format 3. Stop older running processes first and keep a pre-upgrade catalog/warehouse backup for rollback. The included demo uses disposable storage.

Binding definitions are immutable. Deletions and merges involving bindings deliberately fail until reconciliation is implemented. New Object Type revisions require explicit binding migration. Reads conservatively protect whole derived objects using source restrictions. Source fields cannot be edited through Actions or generic object writes; non-source fields retain the existing authorized-write behavior.

## Next session

First reproduce this suite and the demo in `~/S.2a`. Review and commit on `feature/ontology-bindings`; do not replace the repository with the earlier source snapshot. Then design explicit deletion reconciliation and binding migration with retained operational edits and provenance. An application UI, scheduling and connectors follow those contracts.
