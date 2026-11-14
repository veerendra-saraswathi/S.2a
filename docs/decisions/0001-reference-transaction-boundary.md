# ADR 0001: dependency-free transactional reference core

Status: implemented for Gate 1 development; not the final storage topology.

Use Python 3.11+ and SQLite WAL for an offline-executable single-node reference of tenant isolation, branch manifests, Ontology state and Actions. One write transaction establishes atomic publication and failure semantics before service distribution.

The decision deliberately postpones Iceberg/object-store adapters, distributed OSv2 indexes and Kubernetes. It does not remove these requirements from the parity matrix. All runtime dependencies are currently standard library. The source launcher avoids requiring a package registry in an air-gapped development environment.

Before production storage substitution, test prepare/publish recovery, index readiness, branch snapshot reads and outbox reconciliation against the same behavioural contracts.
