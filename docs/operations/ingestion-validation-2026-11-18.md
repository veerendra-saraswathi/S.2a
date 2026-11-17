# File ingestion validation — 18 November 2026

Baseline: `c004d9a6121875d242a918cbb8492346b931c44d` on `feature/ontology-bindings`.
Release: v0.4.0, bounded local connector-fabric slice.

- Full regression suite: **359 tests passed in 57.279 seconds** with `S2_REQUIRE_STORAGE_TESTS=1`; no skipped storage suites.
- New coverage: 21 ingestion cases across JSON, Parquet and Iceberg (**63 additional tests**).
- Proprietary headers: **33 files verified**.
- Iceberg ingestion demo: two imported records, two materialized objects, equal retry/receipt result, valid 18-event audit chain, no broken or unreferenced storage artifacts.
- `git diff --check`: passed.

Coverage includes CSV scalar conversion, native JSON typing, strict malformed-input handling, content/row limits, duplicate keys, all-or-nothing rejection, Unicode, preview binding, stale heads, classification changes, current authorization on replay/receipt reads, hidden-row protection, roles/tenants/membership, receipt ownership, audit/storage rollback, concurrent publication, branch isolation, downstream bindings and CLI preview/apply/token handling.

Existing dependency deprecation and sandbox CPU-cache probe warnings do not affect the passing result. This is local functional evidence, not a production-scale, certification, customer-acceptance or full platform parity claim. Rejected reports are transient; durable quarantine, remote connectors and scheduling are not implemented.
