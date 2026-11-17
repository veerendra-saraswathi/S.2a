# s2-catalog-storage

**Status:** Gate 1 reference implemented.

Immutable JSON, local Parquet and local Iceberg v2 dataset snapshots, transaction log, direct-fork branching and atomic manifest publication. `s2_catalog_storage/file_storage.py` implements sealed file preparation, verified reads and read-only recovery inventory.

The Python package in this subsystem, where present, is the executable Gate 1 implementation. The boundary directories below reserve the approved decomposition; their README files do not represent implemented services.

- `catalog/`
- `dataset-transactions/`
- `snapshot-manifests/`
- `branch-coordinator/`
- `merge-engine/`
- `storage-adapters/`
- `retention/`

Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved — Proprietary Commercial Software.

Reviewed binding-aware direct-fork publication is implemented in `s2_catalog_storage/merges.py`. See [contract](../../docs/architecture/reviewed-merges.md). General merge-base discovery and row/property conflict resolution remain outside this slice.

In v0.5.1, reviewed merges include pipeline coherence proofs and explicitly authorize pipeline resources. The legacy merge route stays guarded. See [contract](../../docs/architecture/pipeline-merges.md).
