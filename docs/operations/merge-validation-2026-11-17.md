# Reviewed merge validation — 17 November 2026

Base: `633115c8d612f948ae2f240439193d6b36d8fb77`, branch `feature/ontology-bindings`.
Release: v0.3.3, local reference runtime.

- Full suite: **296 tests passed in 42.126 seconds**, with `S2_REQUIRE_STORAGE_TESTS=1`; no skipped storage suites.
- 51 new merge tests: 17 cases each across JSON, Parquet and Iceberg.
- Proprietary header checker: **30 files verified**.
- Combined Iceberg binding/reconciliation/migration/merge demo: successful; replay equality, stable identity, preserved approved inspection and unchanged source branch confirmed.
- Demo audit: valid, **68 events**. No external effects. Disposable database removed.
- `git diff --check`: passed.

Coverage includes two-parent atomic publication, preview without revision writes, disjoint-object changes, same-object conflicts, unmaterialized source rejection, reviewed absence retention, completed binding migrations, preservation of approved Actions, stale source/target heads, wrong plan hash, changed policies, current authorization on retry, roles/tenants/projects, audit rollback and concurrent publication.

Dependency deprecation and sandbox CPU-cache probe warnings were emitted by the existing storage dependencies; tests passed. These are local test results, not CI, customer acceptance, distributed scale, security certification or production readiness evidence. The full platform parity target remains open.
