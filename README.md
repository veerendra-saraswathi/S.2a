# Samanvaya 2 — Enterprise Data Operating System

**Owner:** Veerendra Kiran Bidarkota. All Rights Reserved — Proprietary Commercial Software.

**Repository:** https://github.com/veerendra-saraswathi/S.2a

**Status:** Gate 1 executable reference foundation, version 0.2.0. This is a development implementation, not a completed Foundry/AIP-equivalent production platform.

The approved ten-subsystem architecture is scaffolded. Four subsystems contain executable Python code: catalog/storage, governance, Ontology and kinetic Actions. The remaining subsystem boundaries are explicitly marked as planned.

## Run now

Python 3.11 or later is required. The JSON reference backend requires no pip installation or network access. The optional Parquet/Iceberg backends require the storage dependencies below. Without them, storage tests are explicitly skipped; use the required-storage command to prevent a partial pass.

```bash
python3 -m unittest discover -s tests -v
python3 tooling/check_headers.py
python3 tooling/s2.py --db .local/demo.db demo
```

The demo creates a synthetic manufacturing tenant, commits a dataset, explicitly creates an Ontology Asset, executes a governed internal Action and creates a scenario branch. It has no external system effects. Every run creates a new synthetic tenant. It does not print an authentication token.

Start a separate development installation:

```bash
python3 tooling/s2.py --db .local/dev.db bootstrap --tenant "Development" --clearance 3 --compartment ENG
python3 tooling/s2.py --db .local/dev.db serve --port 8080
```

Bootstrap is a host-administrator operation. It prints the initial administrator credential once; keep that credential private. The database stores only its SHA-256 hash. The API listens on **127.0.0.1 only** and uses a bearer token. It is not a production TLS/OIDC gateway.

`GET /health` returns development liveness. `POST /v1/operations` accepts a JSON envelope and an `Authorization: Bearer ...` header. There is no HTTP tenant-bootstrap endpoint.

```json
{
  "operation": "projects.create",
  "params": {"name": "Engineering pilot"}
}
```

See [API contracts](docs/api-contracts/gate1.md) for operations and examples. Tokens determine tenant and principal context; requests cannot select an arbitrary tenant.

## Parquet / Iceberg storage upgrade

Install in a virtual environment from this repository (initial installation needs package access):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-storage.lock
S2_REQUIRE_STORAGE_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tooling/s2.py --db .local/iceberg-demo.db --storage iceberg demo
.venv/bin/python tooling/s2.py --db .local/iceberg-demo.db storage-report
```

Use `--storage parquet` for standalone Parquet or omit the option for the original JSON backend on a new database. The selected file backend and absolute warehouse location are persisted, so subsequent commands can omit both. Default warehouse: `<absolute-database-path>.warehouse`.

Files are written, checksummed, fsynced and read back before SQLite publishes their descriptor and audit entry. The read-only storage report identifies missing/corrupt committed snapshots and unreferenced artifacts. It never deletes anything. Original JSON snapshots remain readable after opt-in; the next successful dataset write creates a file-backed revision.

**Stop existing servers and back up the database before opting an existing installation into file storage.** Enabling file storage advances the database format to version 2, which the old Gate 1 application refuses to open. Do not downgrade the application or move an Iceberg warehouse: metadata contains absolute local URIs. Keep catalog and warehouse backups together.

See [storage design and recovery](docs/architecture/file-storage.md) and [storage validation](docs/operations/storage-validation.md). The storage package is a local reference implementation, not a production distributed lakehouse.

## Implemented foundation

| Capability | Working behaviour |
| --- | --- |
| Tenant/project isolation | Hashed credentials, current principal checks, project membership, admin/editor/viewer roles |
| Mandatory Markings | Classification level and compartments; row/object suppression, property redaction and endpoint-filtered links |
| Current security policy | Project-wide monotonic restriction floors apply across branches, independently of branch snapshots |
| Dataset transactions | Optional sealed Parquet/Iceberg snapshots plus legacy JSON; APPEND/UPDATE/DELETE, OPEN/PREPARED/COMMITTED/ABORTED lifecycle, COMMIT finalization, idempotency and immutable snapshots |
| Cross-resource branches | Pinned manifests containing dataset, schema, object, link and Action revisions; atomic compare-and-swap publication |
| Branch merges | Direct-fork three-way resource merges, explicit conflicts, target/source head checks and schema validation |
| Ontology | Scalar property schemas, interfaces, typed links and optimistic object revisions |
| OSv2 foundation | Authoritative transactional object state and permission-filtered reads; SQLite reference implementation |
| Kinetic Actions | Typed property-set edits across up to 100 objects, atomic commit, replay-safe execution |
| Approvals | Distinct-admin quorum, no submitter self-approval, effect hashes and execution-time revalidation |
| Audit | Append-only application records, per-tenant hash chain, verification, mutation/audit atomicity |
| Offline development | JSON uses the standard library; file backends run locally after dependency installation |

## Deliberate boundaries

- Dataset snapshots support JSON, local Parquet and local Iceberg v2. The file backends write a complete immutable artifact per S2 snapshot. Iceberg table history is per artifact; S2 maintains dataset ancestry. Distributed/object-store catalogs, incremental file reuse, compaction and production query pushdown remain unimplemented.
- OSv2 is the S2 component name. Billion-object scale, sub-100 ms performance, secondary full-text/vector/spatial indexes, media and time-series storage are **not implemented or benchmarked**.
- Merge is conservative at resource granularity. It does not merge two conflicting row sets or arbitrary criss-cross histories. Branches are editable workspaces; no protected-main promotion workflow exists yet.
- All resources are managed through versioned API mutations. A branch manifest can accommodate transforms/apps/AI definitions later, but these are not yet executable resource kinds.
- Object materialization from source datasets is explicit in the demo. Automated source bindings, source/edit precedence and ingestion pipelines arrive later.
- Actions support a declarative typed `set` primitive. No arbitrary Python/TypeScript Functions sandbox, external write-back, remote outbox dispatcher or physical-system control is enabled.
- Approval queues currently consist of persisted pending invocations and decisions, not a configurable queue administration UI or an inbox API.
- OIDC/SSO, TLS termination, encryption/key management, signed offline releases, license enforcement, tenant quotas, horizontal scaling, HA and recovery objectives remain production engineering work.
- Audit verification detects modified retained records. It does not prove the absence of truncation by a database administrator. Independent signed checkpoints and retention infrastructure remain planned.
- Current policy restrictions apply to every project branch. Declassification is intentionally unsupported. Historical revisions are retained internally, but there is no public arbitrary historical-snapshot read endpoint yet.

## Repository map

- `src/s2/`: application facade, development HTTP adapter, CLI and synthetic demo.
- `platform/s2_platform/`: errors, request context, canonical serialization and Marking primitives.
- `subsystems/`: the ten approved subsystem ownership boundaries.
- `contracts/`: API and event contract locations.
- `docs/`: architecture decisions, metamodel, current scope and deployment notes.
- `domains/`: planned vertical packages; no forks of the platform core.
- `tests/test_foundation.py`: executable integration, isolation, concurrency and failure-injection tests.
- `.github/workflows/ci.yml`: test/header/demo checks on Python 3.11–3.13. CI configuration is provided; remote execution is not claimed.

## Development packaging

The launcher is the preferred offline path. With setuptools already available, the optional editable install is:

```bash
python3 -m pip install --no-build-isolation --no-deps -e .
s2 --db .local/demo.db demo
```

Read [Gate 1 architecture](docs/architecture/gate1.md), [metamodel](docs/metamodel/gate1.md), [parity status](docs/parity-matrix/gate1.md), [security](SECURITY.md) and [commercial licensing](LICENSE) before extending the implementation.
