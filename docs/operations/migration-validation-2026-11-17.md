# Migration validation — 17 November 2026

S2 v0.3.2 builds on `ae990bba2ac64788cc7a87bbe55daba0a493456b` on feature/ontology-bindings.

## Local validation

- Full suite with `S2_REQUIRE_STORAGE_TESTS=1`: **245 tests passed**, no skips, 31.292 seconds.
- Prior 197 tests retained; 48 new tests run 16 migration scenarios on JSON, Parquet and Iceberg.
- Proprietary header check: **28 files verified**.
- Combined Iceberg demo: same object identity; MISSING to PRESENT reconciliation; approved inspected status preserved; reading and reading_copy 30; review_note unreviewed; changed binding revision; replay succeeds; audit valid with **54 events**.
- No external effects. Demonstration catalog and warehouse are temporary and removed after execution.

New tests cover no-revision-write previews, one-commit publication, approved-edit preservation, normal post-migration refresh, ownership/default validation, type/field/identity validation, stale head/effect hash, policy changes between preview and apply, restart/replay and changed requests, current authorization on replay, branch/tenant/role isolation, audit and second-object-write rollback, missing-object rejection, catalog format monotonicity, concurrent publication, new source-owned properties and invalid second-row atomicity.

The full suite ran in the development workspace using the available Python environment and existing locked storage dependencies. These are functional local results, not a remote CI run, capacity benchmark or RCI acceptance. Dependency deprecation and sandbox CPU-cache warnings did not fail validation.

## Delivered boundary

Migration adopts compatible current schemas, remaps source-owned fields and adds explicitly owned fields. It preserves existing identity, ownership, operational values, source-presence history and restrictions. It does not edit source dataset schemas, transfer field ownership, change identity, remove properties, migrate still-missing objects or merge branches containing bindings.

Successful migration advances the catalog to format 5; preview/failure does not. Later creation/materialization/reconciliation preserves format 5. Stop old processes and keep catalog plus warehouse backups before using it on persistent data. Old executables cannot reopen the new format.

## User handover

Apply the package only to the clean ae990bb feature-branch checkpoint. Reproduce the 245 tests, 28-header check and combined demo in ~/S.2a. Review and commit before pushing. No commit, push or merge is performed by the installer.

Next engineering priority: design binding-aware branch promotion/merge with explicit conflict detection and preserved data, ownership, current policy and replay semantics. General identity and missing-object migration remain separate design work.
