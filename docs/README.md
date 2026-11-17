# Samanvaya 2 documentation

**Version covered:** v0.3.1 · **Reviewed:** 17 November 2026 · **Stage:** Gate 1 reference foundation.

[Repository landing page](../README.md) · [Quick start](guides/QUICKSTART.md) · [Documents & presentations](resources/README.md)

## Start here

| Reader | Suggested path |
| --- | --- |
| Platform reviewer | [Technical overview](TECHNICAL_OVERVIEW.md) → [Architecture](architecture/platform-overview.md) → [Validation](operations/storage-validation.md) |
| Developer | [Quick start](guides/QUICKSTART.md) → [API reference](api-contracts/gate1.md) → [Metamodel](metamodel/gate1.md) |
| Systems engineer | [Transaction boundary](architecture/gate1.md) → [File-storage protocol](architecture/file-storage.md) → [Security](../SECURITY.md) |
| Programme stakeholder | [Current status](parity-matrix/gate1.md) → [Roadmap](ROADMAP.md) → [Document register](resources/README.md) |

- [Ontology binding contract](architecture/ontology-bindings.md): APIs, ownership, policy propagation and current limits.
- [Bindings checkpoint](operations/session-checkpoint-2026-11-17.md): 164-test validation and session handover.

- [Source reconciliation](architecture/source-reconciliation.md): missing objects, explicit retention and source return.
- [Reconciliation validation](operations/reconciliation-validation-2026-11-17.md): local test evidence and limits.

## Architecture and implementation

- [Platform architecture](architecture/platform-overview.md): system topology and trust boundaries.
- [Technical overview](TECHNICAL_OVERVIEW.md): executable behaviour and engineering contracts.
- [Technology stack](TECH_STACK.md): current and planned technologies, with dependency links.
- [Gate 1 implementation architecture](architecture/gate1.md): ownership and the transaction boundary.
- [File-backed snapshots](architecture/file-storage.md): Parquet/Iceberg design and recovery.
- [Branch-aware metamodel](metamodel/gate1.md): persisted records and resource payloads.
- [ADR 0001](decisions/0001-reference-transaction-boundary.md): why the reference core uses a single transactional catalog.

## Interfaces and operations

- [Quick start](guides/QUICKSTART.md): installation, tests, demo and local API.
- [API reference](api-contracts/gate1.md): operations, parameters and retry semantics.
- [Operation envelope schema](../contracts/json-schema/operation-envelope.schema.json): machine-readable request envelope.
- [Development deployment](deployment/development.md): offline/runtime boundaries and backup notes.
- [Security guidance](../SECURITY.md): authentication, data protection and operational limits.
- [Contribution rules](../CONTRIBUTING.md): ownership, authorization and verification expectations.

## Evidence and delivery

- [Original foundation validation](operations/gate1-validation.md): historical v0.1 baseline of 26 tests.
- [Storage upgrade validation](operations/storage-validation.md): v0.2 baseline of 104 tests.
- [Storage dependency inventory](operations/storage-dependencies.json): recorded package metadata.
- [Implementation status matrix](parity-matrix/gate1.md): delivered and remaining capabilities.
- [Roadmap](ROADMAP.md): proposed delivery sequence and acceptance gates.
- [Session checkpoint](operations/session-checkpoint-2026-11-15.md): current milestone and the next starting point.
- [Document register](resources/README.md): future Word, PDF and PowerPoint additions.

## How to interpret status

**Implemented reference** means executable functionality exists and has local test evidence. **Planned** means an architecture boundary or proposed capability exists without a delivered implementation. **Production-qualified** requires separate security, deployment, capacity and recovery evidence; this repository does not claim that status.

The current code and versioned validation records describe implementation status. Future client proposals and presentation materials will be listed with their covered version and scope so that roadmap statements are not mistaken for delivered features.
