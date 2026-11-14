# Storage upgrade validation — v0.2.0

Date: 15 November 2026. Local platform: Intel macOS, Python 3.13.0.

The full required-storage suite exercises **104 tests**: the original 26 foundation tests on JSON, those same 26 on Parquet and Iceberg, and 13 additional storage tests on each file backend. The storage dependency flag prevents silently skipping file-backend tests.

```bash
S2_REQUIRE_STORAGE_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

Validated behaviours:

- Real Parquet magic/metadata and independent PyIceberg StaticTable reads.
- Empty snapshots, optional fields, Unicode, integer limits and exact canonical round-trips.
- APPEND/UPDATE/DELETE lifecycle; pinned descriptors and historical file retention.
- Fork isolation, descriptor merges, tenant/project restrictions and current Markings on older branches.
- File preparation failure, unpublished sealed artifacts after audit failure, and missing/corrupt file rejection.
- A subprocess killed immediately after sealing and before publication; database reopening leaves the old head and the prepared transaction can retry.
- A subprocess killed after database commit but before response delivery; retry returns the committed result without another file write.
- Legacy inline snapshots remain readable through JSON-to-file opt-in; the next mutation creates a file-backed revision.
- Pinned warehouse/backend configuration, path traversal/symlink rejection and cross-tenant descriptor rejection.

The synthetic Iceberg demo also completed with a committed Action and valid audit chain. Host sandbox restrictions produced Arrow CPU-cache probing warnings; they did not fail the format reads or tests.

This is functional/process-crash validation on one local machine. It is not distributed-storage, cloud IAM, abrupt hardware power-loss, performance, disaster-recovery or production-security qualification. Direct dependencies are pinned in pyproject.toml; the complete tested environment is recorded in requirements-storage.lock. Dependencies are not bundled in the source ZIP.
