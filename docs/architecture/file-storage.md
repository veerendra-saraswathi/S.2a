# Immutable file-backed dataset snapshots

Version 0.2.0 — 15 November 2026.

## Scope and formats

S2 now supports `json`, `parquet` and `iceberg` dataset storage. The default remains JSON for compatibility and dependency-free development. The storage extra provides real Parquet files and Apache Iceberg format-v2 tables through PyArrow and PyIceberg. Both file backends are local-filesystem reference adapters.

Each S2 snapshot owns a unique tenant/UUID artifact directory. Parquet artifacts contain a typed data file. Iceberg artifacts contain data files, Avro manifests, manifest lists and versioned JSON metadata. An ephemeral SQL catalog constructs each Iceberg artifact; it is discarded after preparation. Serving reads use `StaticTable` with a pinned metadata file and snapshot ID, never that temporary catalog's head. An empty Iceberg table has metadata but no data snapshot ID.

This deliberately creates a complete table per S2 snapshot. It provides independent immutable branch references but is not incremental table-level Iceberg branching. Dataset ancestry is recorded in S2's parent snapshot references, not as one continuous Iceberg table history. There is no remote REST/Hive/Glue catalog, S3 adapter, partition planner, compactor, deletion-vector writer or distributed transaction coordinator in this delivery.

## Physical schema and exactness

Source properties map to stable-in-this-schema physical columns `c0`, `c1`, ... in sorted property-name order. The seal records this mapping. String, signed int64, float64 and boolean columns are typed. `_s2_row` contains the canonical row envelope, including Markings; `_s2_ordinal` preserves deterministic row order across file scans. The canonical envelope distinguishes omitted optional properties from null and preserves integer versus floating numeric representation. The snapshot is read back and compared before publication.

File-backed integer properties must fit signed 64 bits. Integer-valued number properties outside the exact float64 range are rejected; use integer type where appropriate. Existing JSON snapshots remain readable without narrowing their historical representation, but a later file-backed write must satisfy the physical type constraints.

## Publication protocol

1. Authenticate and validate the request inside the S2 catalog writer transaction.
2. Resolve the pinned input snapshot and apply mutations in memory.
3. Allocate a fresh artifact directory. No serving manifest references it yet.
4. Write data and, for Iceberg, metadata/manifests at their final absolute location.
5. Hash and fsync files; write/fsync `SEALED.json`; sync directory entries.
6. Open the artifact through the real reader, check its file digests, pinned snapshot ID, row count and logical row hash.
7. Store only schema, parent and storage descriptor in the new dataset revision. No inline snapshot rows are stored for that revision.
8. Publish the branch head and transaction outcome with the audit entry in one SQLite commit.

This is an ordered file-prepare/catalog-publish protocol, not an atomic transaction across two independent databases. SQLite remains the sole publication authority. Files are never overwritten by retries. A crash before the catalog commit leaves an unreferenced artifact; a crash after commit leaves a complete referenced snapshot and a replayable transaction outcome. Interrupted artifacts do not become visible merely because their files exist.

All dataset rows are currently materialized in memory, and full snapshots are rewritten. The catalog writer lock is held during preparation and integrity checks. These choices prioritize semantics over throughput; they are not the final distributed architecture. Staged transaction payloads remain in the SQLite transaction ledger even after snapshot data is externalized.

## Security and branch behaviour

The existing tenant/project and Marking checks remain authoritative. Hydration restores raw rows inside the trusted server, then applies current row/property restrictions. Direct file access bypasses those checks and must remain restricted to the service/host administrator. Both data and metadata are plaintext in this reference implementation.

Descriptors are tenant-scoped, relative to a pinned warehouse, validated against path traversal/symlinks and verified against a seal checksum. The seal covers all files created for the artifact. Policy floors remain live across older branches. Forking shares immutable descriptors; successful writes on either branch create new artifacts. Merging publishes the selected descriptor without replaying external effects.

## Compatibility and recovery

The storage configuration is pinned in the database. Omitting CLI flags on restart reloads it. JSON-to-file opt-in is supported; changing an already-file-backed backend or warehouse requires a future explicit migration. File enablement advances the database format to version 2 so the original v0.1 application refuses to open it. Stop existing server processes before enablement; new v0.2 instances also reject requests after their cached backend becomes stale.

`storage-report` holds the local writer lock, verifies every retained file-backed revision (including historical and forked references) and lists unreferenced directories. It is read-only and returns nonzero from the CLI if a committed reference is broken. It does not delete, restore or automatically publish anything.

For a pre-publication crash, inspect the report and retry the same prepared transaction if its branch head is still current. A stale head requires abort/restart; an uncertain response after commit should be retried with the original transaction ID. For missing/corrupt committed files, restore the exact artifact from backup; never silently substitute the latest snapshot or fabricate empty rows.

Back up the database and warehouse together with the application stopped. Iceberg metadata embeds absolute paths, and this release does not rewrite them on relocation. Restore at the same warehouse path and run the report. No automatic retention or orphan garbage collection is enabled. OS fsync and the tested process-crash protocol do not constitute hardware power-loss or disaster-recovery certification.

## Source references

Public API guidance used for implementation; installed-version behaviour was exercised by tests:

- Apache PyIceberg API: https://py.iceberg.apache.org/api/ — Arrow writes, StaticTable and pinned snapshot scans.
- Apache PyIceberg SQL catalog: https://py.iceberg.apache.org/reference/pyiceberg/catalog/sql/ — temporary local catalog construction.
- Apache PyIceberg configuration: https://py.iceberg.apache.org/configuration/ — catalog and FileIO configuration.

These sources describe upstream libraries. S2's sealing/publication/recovery protocol is independently implemented here.
