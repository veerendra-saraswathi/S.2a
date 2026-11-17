# Pipeline validation — 18 November 2026

Baseline: `f87ef6ea56dc75b5f8d5165ba64e1fce01768aa5`, `feature/ontology-bindings`.
S2 software release: v0.5.0; first bounded Compute & Pipelines slice.

- **428 tests passed in 67.192 seconds**, with `S2_REQUIRE_STORAGE_TESTS=1`; no skipped storage suites.
- **69 new pipeline tests**: 23 cases across JSON, Parquet and Iceberg.
- **36 proprietary headers verified**.
- Iceberg demo: two ingested rows, one accepted output row, one Ontology object, matching replay and receipt, valid **23-event** audit chain.
- Storage demo: four referenced artifacts, no broken references, no unreferenced artifacts, no deletions.
- `git diff --check`: passed.

Coverage: pinned previews and receipts, atomic replacement, refreshed-input reruns, versioned filters, branch isolation, health failure preservation, explicit empty output, invalid definitions, type-preserving rename, missing required projection values, row bounds, stale heads, wrong hashes, source/output/pipeline policy changes, current authorization on replay, static markings on empty output, transitive derived-object restrictions, restart replay, role/tenant/membership boundaries, receipt ownership, audit/storage rollback, concurrent publication, materialized cycle detection and explicit pipeline-merge rejection.

Defining the first pipeline advances the catalog to format 6; old runtimes must refuse it. Application-data branch merges involving pipelines are intentionally unsupported pending coherence checks. This does not prevent Git operations. Existing non-pipeline behavior passes its regression tests.

Existing dependency deprecation and sandbox CPU-cache probe warnings were emitted; the suite and demo passed. This is local functional evidence, not distributed performance, customer acceptance, security certification or full platform parity. Synthetic demo data is disposable and produces no external effects.
