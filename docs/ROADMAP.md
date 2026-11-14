# Development roadmap

[Repository](../README.md) · [Documentation index](README.md) · [Current implementation matrix](parity-matrix/gate1.md)

This roadmap describes the engineering sequence for the proprietary S2 enterprise platform. It is not a delivery-date, budget or production-acceptance commitment. The current baseline is **v0.2.0**, validated locally with 104 tests.

## Delivered foundation

| Milestone | Delivered evidence |
| --- | --- |
| v0.1 — Transactional reference core | Tenant/project isolation, Markings, dataset transactions, branch manifests, Ontology primitives, internal Actions and audit; original 26-test baseline |
| v0.2 — Local file-backed snapshots | Parquet/Iceberg artifacts, pinned reads, sealing/integrity checks and crash/retry behaviour; 104-test baseline and successful Iceberg demo |

Gate 1 remains a reference foundation. Production scale, high availability and full platform parity are not implied by these milestones.

## Next — Dataset-to-Ontology bindings

**Objective:** materialize governed objects from committed dataset snapshots through versioned mappings.

| Engineering requirement | Acceptance evidence |
| --- | --- |
| Versioned source binding | Dataset fields map to typed object properties with deterministic identity and schema checks |
| Pinned materialization inputs | A build records the exact dataset, binding and Ontology revisions it used |
| Branch isolation | A scenario build cannot alter objects on another branch |
| Source/edit precedence | A new source value cannot silently overwrite an approved operational edit |
| Deletions and conflicts | Source disappearance, invalid rows and concurrent edits produce explicit outcomes |
| Security propagation | Source and object Markings remain enforced, including current restrictions on older branches |
| Retry and failure recovery | Repeated processing is replay-safe; an interrupted build does not publish a partial object set |
| Provenance | Each materialized object/property can be traced to its binding and source snapshot |

A suitable synthetic demonstration is a maintenance asset dataset whose committed changes create or update typed Asset objects, while an approved Action edit is preserved or explicitly reconciled according to policy.

## Subsequent delivery gates

| Gate | Scope | Evidence needed before claiming completion |
| --- | --- | --- |
| Integration and data engineering | Connector agents, CDC checkpoints, HyperAuto proposals, pipeline DAGs and Data Health | Repeatable source ingestion, drift handling, reproducible builds and failure recovery |
| Developer platform and applications | Typed TypeScript/Python/Java SDKs, Workshop/Slate and Contour/Quiver foundations | Schema compatibility tests and working applications bound to governed Objects and Actions |
| Governed AI | Model routing, Logic, Automate, Evals and local inference | Permission-constrained retrieval/tools, traceable outputs, evaluation gates and approved Action execution |
| Deployment qualification | SaaS/dedicated/air-gapped packaging, identity, keys, licensing, observability and recovery | Signed release process, installation/upgrade tests, tenant isolation and deployment-specific security evidence |
| Capacity and reliability | Distributed storage/serving, indexes, HA and workload tuning | Defined workload profiles, measured latency/capacity and restore/failover evidence |

## Cross-cutting engineering requirements

Preserve the transaction and authorization invariants as the implementation grows. New UI components and AI agents must use the same governed Actions as other callers. Moving data into distributed storage must retain a coherent publication boundary and explicit serving readiness. External write-back needs durable intent, idempotency and reconciliation rather than assuming a remote system participates in S2's local transaction.

For every milestone, update the implementation matrix, technical documentation, test evidence and dependency notices together. Product presentations should identify the version they describe and distinguish demonstrated behaviour from roadmap scope.
