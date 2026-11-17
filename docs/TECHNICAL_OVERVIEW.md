# Technical overview — Samanvaya 2

[Repository](../README.md) · [Architecture](architecture/platform-overview.md) · [Tech stack](TECH_STACK.md) · [API reference](api-contracts/gate1.md)

| Document control | Value |
| --- | --- |
| Product | Samanvaya 2 — Enterprise Data Operating System and Operational Ontology Platform |
| Version covered | 0.3.3 |
| Review date | 17 November 2026 |
| Maturity | Executable Gate 1 reference foundation |
| Ownership | Veerendra Kiran Bidarkota — Proprietary Commercial Software |
| Validation baseline | 296 local tests; successful combined Iceberg reconciliation and migration demo |

## 1. Purpose and current scope

S2 establishes a common platform for versioned enterprise data, typed operational objects and authorized state changes. The full product architecture includes integration, data engineering, Ontology, application builders, developer SDKs, analytics and governed AI. The delivered slice establishes the shared transaction and security semantics on which those capabilities will depend.

The reference implementation supports local execution with JSON, Parquet or Iceberg-backed dataset snapshots. It includes isolated tenants/projects, branch manifests, object schemas/interfaces/links, internal Actions with approvals and audit evidence. It does not yet implement automated ingestion, a graphical console or an LLM runtime.

## 2. Request and authorization model

The application entry point is `Service.call(token, operation, **params)`. The local HTTP adapter maps `POST /v1/operations` to that boundary. Tenant and principal context are derived from the bearer credential, not accepted as caller-selected parameters.

Each operation validates its registered parameters, checks the current principal's active status and role, and checks project membership where a branch is involved. Non-admin principals require explicit project membership. Admin role does not bypass classification restrictions.

Markings combine an ordinal classification level with required compartments. Reads omit inaccessible rows/objects and redact inaccessible properties. Links require authorized link/link-type state and visible endpoints. Current project restriction floors apply across branches, so opening an old branch does not recover access that was subsequently restricted.

The development HTTP adapter is loopback-only. Production federated identity, TLS termination, token lifecycle administration and external policy engines remain future work. See [SECURITY.md](../SECURITY.md).

## 3. Versioned datasets and transaction semantics

A dataset revision contains a scalar schema, primary key and parent snapshot reference. Its data is either inline JSON or a descriptor for a sealed file artifact. A platform commit maps resource identities to specific revision digests.

Dataset mutations use an explicit lifecycle:

| State / operation | Meaning |
| --- | --- |
| `OPEN` | Mutation intent registered against a pinned branch head |
| `PREPARED` | Staged rows validated against the prospective resulting state |
| `COMMITTED` | New snapshot, branch publication, outcome and audit committed |
| `ABORTED` | Open/prepared work explicitly cancelled |
| `APPEND` | Add rows with new primary keys |
| `UPDATE` | Replace complete rows at existing primary keys |
| `DELETE` | Remove existing rows using primary-key values |
| `COMMIT` | Finalize a prepared transaction; not a fourth row-mutation mode |

Idempotency keys bind transaction creation to the actor, branch, base and operation. A committed transaction returns its recorded outcome on retry. A stale branch head cannot silently overwrite newer state. Cancellation cannot undo an already committed transaction.

File-backed integer properties use signed 64-bit values; numeric properties use float64 with validation of integer-valued exactness. Canonical row envelopes preserve optional-field absence, numeric representation and Markings. Full details are in the [file-storage design](architecture/file-storage.md).

## 4. Branches, publication and consistency

A branch points to an immutable platform commit. Its manifest resolves datasets, schemas, object/link revisions and Action definitions together. New revisions are prepared before publication; the branch head changes only when its expected current value matches.

The SQLite reference uses one writer transaction to coordinate authoritative state. For file snapshots, the application prepares and verifies files before committing their descriptor. This establishes a precise publication boundary without claiming a distributed transaction across multiple storage systems.

Forks initially share the same commit. Direct-fork three-way merges combine disjoint resource changes and reject conflicts. There is no automated row-level resolution for two independently modified dataset snapshots, and no general criss-cross merge support. Resource deletions and breaking schema changes require future migration workflows.

Operational audit, approval decisions and external outcomes are not branch-rewindable data. This release has no external side effects to replay during a merge.

## 5. Ontology and object state

The semantic model provides Object Types, property definitions, Interfaces and Link Types. Supported scalar properties are string, integer, number and boolean. Required values must be present; unknown properties and invalid scalar types are rejected.

Interfaces declare shared property contracts. Implementations must satisfy property types, required flags and Markings. Links enforce endpoint types. Object replacement checks the expected object revision in addition to the branch head.

The OSv2 foundation is the authoritative scalar object store and its governed read paths. Advanced search/vector/spatial/media/time-series projections remain planned. Names currently act as schema identifiers, so stable-ID rename/move migrations are still required for general schema evolution.

A bounded dataset-to-Ontology materializer now supports explicit builds from committed snapshots. The original demo still creates objects explicitly; `tooling/bindings_demo.py` demonstrates automatic object construction on demand. Continuous synchronization is not implemented. See [binding contracts](architecture/ontology-bindings.md).

## 6. Actions and approval controls

An Action definition selects one object type, allowed writable properties and an approval quorum. Submission validates the proposed edits, captures the target revisions and computes an effect digest. At most 100 objects can be edited by a single invocation in this reference implementation.

When approvals are required, distinct active administrators approve the same pinned effect. Submitters cannot approve their own invocations. Execution is restricted to the submitting principal and rechecks current authorization, eligible approvals, property validation and the proposed effect.

A change to the pinned branch state invalidates pending execution. This intentionally conservative rule can require resubmission after unrelated branch activity. Successful internal object edits publish atomically with the new branch head and audit evidence. A repeated execution request returns the committed result.

Configurable queue administration, expiry/rejection/cancellation APIs, sandboxed Python/TypeScript Functions and transactional external write-back remain planned. Approval records alone should not be presented as a complete enterprise workflow product.

## 7. Audit and provenance

Each successful application request records an audit event. Mutation publication and successful audit recording share the same database transaction. Authenticated domain rejections are recorded after rollback. Audit entries include actor, tenant, operation, resource context, timestamp and outcome metadata.

Per-tenant hashes link retained events. Application triggers prevent ordinary update/delete operations on audit records. Verification detects modifications to retained content; independently retained signed checkpoints are still needed to detect a privileged rewrite or truncation reliably.

Snapshot parent references, commit manifests and transaction outcomes provide foundational provenance. A full interactive lineage DAG spanning connectors, transforms, widgets, models and Actions is a later Governance Control capability.

## 8. Recovery and operational behaviour

The storage report verifies all retained file-backed revisions, including historical references. It lists broken references and unreferenced artifact directories while holding the local catalog writer lock. It never deletes, repairs or publishes data automatically.

| Failure point | Expected behaviour |
| --- | --- |
| Before sealing finishes | No new branch publication; incomplete artifact may remain |
| After sealing, before catalog commit | Previous head retained; sealed orphan may remain; prepared transaction may retry |
| After catalog commit, before response | Retry returns the recorded committed result |
| File deleted or corrupted after commit | Read fails; report identifies the broken retained reference |
| Audit write failure during mutation | Catalog mutation and branch publication roll back |
| Missing required approval or current access | Action does not publish edits |

Stop the application and back up both catalog and warehouse. File storage pins an absolute warehouse location; Iceberg metadata contains absolute local URIs. Restore to that location and verify integrity. Automated relocation, remote storage recovery and production RPO/RTO commitments are not delivered by this version.

## 9. Verification and qualification

The v0.3 suite adds 60 binding tests (20 per backend), for 164 passing tests. The original 104-test baseline comprises 26 foundation tests on each of JSON, Parquet and Iceberg, plus 13 storage-specific tests on each file backend. Coverage includes tenant isolation, classification, branch conflicts, atomic edits, approval revalidation, retries, real-format inspection, corrupted files and subprocess crashes on both sides of catalog commit.

The successful local demonstration additionally confirmed an internal maintenance Action, a valid audit chain and a clean Iceberg storage report. The [validation record](operations/storage-validation.md) documents the environment and limits.

These checks establish reference behaviour. They do not establish distributed throughput, billion-object capacity, production latency, security certification or unrestricted operational deployment. Those properties need explicit workloads, deployment conditions and acceptance thresholds.

## 10. Next implementation contract

Bindings now supply immutable mappings, deterministic identity, pinned inputs and atomic materialization. Source refresh preserves Action-owned fields. Source disappearance is rejected by default; administrators can explicitly retain reviewed missing objects. Merges involving bindings remain rejected. See [reconciliation](architecture/source-reconciliation.md).

Acceptance should demonstrate mapping validation, branch isolation, current-policy enforcement, replay-safe builds, source/delete handling and a failure that leaves the previous consistent publication intact. See the [roadmap](ROADMAP.md) for the wider delivery sequence.

## 12. Compatible binding migration

Administrators can preview and apply compatible mapping/schema changes with an effect hash and idempotency key. The binding and all objects publish in one commit; existing ownership, identity, operational values and policy remain protected. See [migration contracts](architecture/binding-migrations.md) for supported additions and explicit exclusions.

## Reviewed binding-aware merges

The administrator previews exact cross-resource changes, then publishes the reviewed plan against pinned source and target heads. Current authorization and binding coherence are revalidated. Same-object conflicts are rejected. See [detailed contract](architecture/reviewed-merges.md).
