# Governed lineage validation — 18 November 2026

Baseline: `9253599b85bbf5f5abec34b81ebe9f18b4689039`, branch `feature/ontology-bindings`.
Release: S2 v0.7.0, bounded recorded-provenance graph API.

- **590 tests passed in 104.193 seconds**, with `S2_REQUIRE_STORAGE_TESTS=1`; no skipped storage suites.
- **45 new tests**: 15 lineage cases across JSON, Parquet and Iceberg.
- **42 proprietary headers verified**.
- Iceberg orchestration/lineage demo: one materialized object, **10 nodes and 9 edges**, valid **37-event** audit.
- Seven referenced storage artifacts; no broken or unreferenced artifacts and no deletions.
- `git diff --check`: passed.

Coverage includes exact object/dataset pins, source leaves, deterministic results after restart, no catalog revision/commit/run writes, historical sources after refresh, live source/pipeline/binding restrictions, hidden-row denial, graph limits, root validation, tenant/project membership, stale heads, operational object edits, compatible binding migration, manual objects, fork/merge history and rejection of injected cross-project references. The graph omits raw values.

Catalog format remains 6 and no dependency is added. Existing dependency deprecation and sandbox CPU-cache probe warnings were nonfatal. The graph reports recorded provenance; it is not a transform-correctness or freshness certificate. Local functional evidence does not imply production qualification, customer acceptance or full platform parity.
