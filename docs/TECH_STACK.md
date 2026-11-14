# Technology stack

[Documentation index](README.md) · [Platform architecture](architecture/platform-overview.md) · [Technical overview](TECHNICAL_OVERVIEW.md)

**Coverage:** S2 v0.2.0, 15 November 2026. Versions below reflect the repository's declared dependencies and recorded local validation environment.

## Implemented runtime

| Component | Technology | Purpose and boundary |
| --- | --- | --- |
| Application runtime | Python 3.11+ | First-party domain logic and application orchestration; tested locally with Python 3.13 |
| Service boundary | `s2.service.Service` | Explicit operation registry, authenticated context, project checks and transaction orchestration |
| CLI | Python `argparse` | Host bootstrap, local service startup, synthetic demo and read-only storage report |
| Development HTTP | `http.server.ThreadingHTTPServer` | Loopback-only `/health` and `/v1/operations`; not a production gateway |
| Catalog/state | SQLite via `sqlite3` | WAL mode, foreign keys, FULL synchronous mode and `BEGIN IMMEDIATE` writer transactions |
| Versioning | Canonical JSON, SHA-256 digests, UUID commit IDs | Immutable resources and manifest-based branch publication |
| Authorization | First-party Python policy checks | Admin/editor/viewer roles, project membership, ordinal classification and compartments |
| Audit | Canonical events, SHA-256 chains and SQLite append-only triggers | Local tamper detection for retained records; independent signed anchors remain planned |
| Test harness | Standard-library `unittest` | Integration tests, concurrency, fault injection and subprocess crash/restart tests |
| Packaging | Setuptools / `pyproject.toml` | Optional wheel/editable package; source launcher avoids installation for JSON mode |
| CI configuration | GitHub Actions | Foundation matrix on Python 3.11–3.13 plus a Python 3.13 storage job |

The CI workflow is versioned in the repository. Local test results must not be interpreted as evidence that a particular remote workflow run succeeded.

## Optional storage dependencies

| Dependency | Pinned version | Used for |
| --- | --- | --- |
| PyArrow | 26.0.1 | Arrow tables, Parquet writes and reads, and local FileIO |
| Apache PyIceberg | 0.10.0 | Iceberg v2 table construction, metadata/manifests and pinned StaticTable reads |
| SQLAlchemy | 2.1.8 | Temporary in-memory SQL catalog used during Iceberg artifact construction |

The temporary Iceberg catalog is a preparation mechanism. The durable serving reference is the S2 dataset revision, which pins a sealed artifact. It is not a second mutable authority for branch state.

- [Declared package requirements](../pyproject.toml)
- [Complete tested storage environment](../requirements-storage.lock)
- [Dependency metadata inventory](operations/storage-dependencies.json)
- [Third-party notices](../THIRD_PARTY_NOTICES.md)

## Data representation

| Representation | Role |
| --- | --- |
| Canonical JSON | Resource definitions, manifests, audit events, legacy inline snapshots and exact row envelopes |
| Parquet | Typed columnar data files for local snapshot artifacts |
| Iceberg v2 JSON metadata | Schema and immutable table/snapshot metadata |
| Avro manifests | Iceberg data-file inventory and snapshot manifests |
| `SEALED.json` | S2 artifact identity, file hashes, logical row hash, column mapping and pinned snapshot information |
| SQLite transaction ledger | Prepared mutations, Action invocations, approvals, commit outcomes and audit records |

File-backed types currently cover strings, booleans, signed 64-bit integers and float64 numbers. Canonical row envelopes preserve Markings, omitted optional fields and numeric representation. Dataset processing still materializes complete snapshots in memory.

## Planned platform technologies

These are architectural candidates or requirements, not installed capabilities of v0.2.0. Selection and qualification belong to the relevant delivery gate.

| Area | Planned direction | Current boundary |
| --- | --- | --- |
| Enterprise ingress | ERP/PLM/SCADA/API connectors, CDC and offline agents | Connector Fabric scaffold |
| Pipeline execution | SQL/Python transforms and Spark/Flink/DuckDB adapters | Pipeline subsystem scaffold |
| Developer SDKs | Generated TypeScript, Python and Java clients | Generator boundaries only |
| Operational applications | TypeScript web application builders, stateful widgets and map/timeline components | Workshop/Slate scaffold |
| Advanced object serving | Search, vector, time-series, spatial and media projections | Authoritative scalar object store only |
| AI execution | Governed model routing, local inference candidates such as vLLM/Ollama, Logic/Automate/Evals | AIP Runtime scaffold |
| Production security | Federated identity, TLS, managed keys, policy administration and signed audit anchors | Local credentials and policy primitives |
| Deployment | Cloud SaaS, dedicated VPC and air-gapped release bundles | Local single-node runtime |

S2's core implementation is proprietary. Dependencies retain their upstream licenses; adding a component requires dependency review and updated notices, rather than applying S2 ownership headers to upstream code.
