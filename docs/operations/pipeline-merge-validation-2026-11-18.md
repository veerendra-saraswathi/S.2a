# Pipeline merge validation — 18 November 2026

Baseline: `ea3873262041d42996a9e78747f22d97d1a991ca`, `feature/ontology-bindings`.
S2 release: v0.5.1; reviewed pipeline-aware branch merges.

- **482 tests passed in 80.615 seconds**, with `S2_REQUIRE_STORAGE_TESTS=1`; no skipped storage suites.
- **54 new tests**: 18 pipeline-merge cases across JSON, Parquet and Iceberg.
- **37 proprietary headers verified**.
- Combined Iceberg merge demo: reading refreshed to 40, same object identity, approved `inspected` status preserved, source branch unchanged and retry equal.
- Valid **47-event** audit chain; six referenced storage artifacts, no broken or unreferenced artifacts, no deletions.
- `git diff --check`: passed.

Coverage includes read-only previews and two-parent commits, input/output and definition pins, stale/missing runs, modified output rejection, binding freshness, target operational conflicts, policy changes, current authorization on replay, competing writers, graph cycles, multi-stage dependencies, rollback, concurrent publication, stale heads, legacy guard and original-branch receipt retention. Fault-injection fixtures additionally verify rejection of wrong stored values, missing policy dependencies and static classification downgrades.

Catalog format remains 6; no new dependency is added. Existing dependency deprecation and sandbox CPU-cache probe warnings were emitted; validation passed. These are local functional results, not customer acceptance, production scale, security certification or full platform parity. Synthetic data is disposable and produces no external effects.
