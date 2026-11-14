# Gate 1 implementation architecture

Date: 15 November 2026. Status: reference foundation, extended with optional file storage in v0.2.0. See file-storage.md for the cross-filesystem publication protocol.

## Ownership and dependency direction

The HTTP adapter and CLI invoke `s2.service.Service`, which provides an explicit operation registry. It resolves authenticated context and project membership, establishes one database transaction and routes to the responsible subsystem. Domain logic is implemented in catalog, governance, Ontology and Actions packages. The facade contains project/branch orchestration; extraction into services can happen behind these contracts later.

The catalog owns SQLite manifest/revision persistence and optional sealed Parquet/Iceberg dataset artifacts. Governance owns identity, authorization, security policy and audit. Ontology owns schema/interface/object/link validation. Actions own Action definitions, invocation state, approvals and edit execution. Packages depend on shared platform contracts; UI and future agents must use the application boundary rather than tables.

## Transaction boundary

A write request starts `BEGIN IMMEDIATE`. Its handler authenticates against current principal state, checks role and project, resolves the expected branch head, validates mutations, stores immutable resource revisions, inserts a platform commit, publishes the new branch head and appends audit evidence. One SQLite commit makes these changes visible together. Any uncaught exception rolls them back. Authenticated domain rejection events are recorded in a separate audit transaction after rollback.

This is a single-node serializable reference boundary with one active writer. It establishes executable semantics, not distributed throughput. Two concurrent publications based on the same expected head produce one winner and one conflict. Persisted snapshots and commits survive service restarts.

## Branches

A branch head resolves to a manifest: `{resource_key: revision_digest}`. A fork points at the same immutable commit until changed. Dataset snapshots, types, interfaces, object states, link types, links and Action definitions are published through the same head. Digest-addressed revisions are scoped to a tenant. Resource names are project-scoped through branch manifests.

Dataset transaction and Action invocation ledgers are operational records, not branch-rewound resources. Merging does not replay Actions or copy approval decisions. A direct-fork three-way merge combines disjoint resource changes; conflicting same-resource changes fail. Changes are validated before the merged manifest is published. Dataset row-level conflict resolution and general merge-base selection remain future work.

## Security

Bearer tokens are cryptographically random; only hashes are persisted. Every request resolves current principal status, role, clearance and compartments. Tenant identity never comes from request parameters. Non-admin principals need explicit project membership. Admin role does not bypass classification.

Classification uses an ordinal level plus required compartments. Dataset rows and objects above the caller's authorization are omitted. Restricted properties are removed. Links require visible endpoints and link/link-type authorization. Write operations conservatively require access to the full target type's declared properties.

Live restriction floors are keyed by tenant, project and resource. `policy.restrict` can only increase them. Floors apply to reads/writes on every branch, including branches created before the restriction. This avoids treating a historical schema as a historical access grant. Type/interface/property/row labels in snapshots remain additional requirements. Declassification needs a separately designed workflow.

## Internal Actions

An Action definition specifies one object type, allowed writable properties and an approval quorum. Submission pins the entire branch head, Action revision, object revisions and proposed-effect digest. The current implementation intentionally invalidates pending execution on any branch movement. This is conservative and may cause resubmissions under unrelated activity.

Distinct active administrators approve; submitters cannot self-approve. Execution is performed by the current authorized submitter and revalidates the quorum, current reviewer access, proposed effect, object types and values. All object revisions and the new branch head commit together. Retrying an already committed invocation returns its recorded outcome without repeating edits.

## Production transition

Do not replace this reference boundary with separately committed writes across services. First introduce versioned contracts, persistent prepare/publication coordination, recovery rules and readiness-aware serving. Moving dataset bytes to Iceberg does not by itself provide atomic platform publication. Search indexes must remain derived views with explicit watermarks; they must not become ungoverned authoritative state.

Production deployment additionally needs managed identity, keys, TLS, resource limits, dependency pinning, signed releases, backup/restore drills, tenant-specific encryption, observability and adversarial authorization testing. None are inferred from passing the Gate 1 reference tests.
