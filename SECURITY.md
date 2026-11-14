# Security and operating boundary

This is proprietary development software. Report security issues privately to the repository owner; do not publish credentials, enterprise data or exploit details in a public issue.

The development HTTP adapter is loopback-only, has a 1 MiB request cap, rejects browser Origin requests and has no CORS grant. It does not implement TLS, federated identity, rate limiting or production request scheduling. Do not expose it directly to an enterprise network or the internet.

The CLI creates new database files under a restrictive umask. Existing files retain their permissions. The SQLite database and its WAL/SHM files contain plaintext enterprise records. OS filesystem protections are the current storage boundary; encryption and external key management are not implemented.

Tokens inherit the current principal's authorization and can be revoked by deactivating the principal. Token expiration, rotation and multiple scoped service credentials are future work. Bootstrap assumes a trusted host administrator. Never expose bootstrap as a normal tenant API.

All dataset/object reads and mutations must use `Service.call`. Raw database access and Python package internals are trusted host-level interfaces, not authorization boundaries. Direct filesystem access can bypass application policy.

Audit triggers block ordinary UPDATE/DELETE operations. Hash verification detects content changes in retained records; independent checkpoints are needed to detect a malicious suffix truncation or complete chain rewrite. Do not claim administrator-proof immutability.

The first-party code contains no autonomous controller output, external Actions or arbitrary user-code execution. Adding those capabilities requires explicit sandbox, identity, transaction and reconciliation designs.

## File-backed snapshots (v0.2)

Parquet files contain typed properties and canonical row envelopes including Markings. Iceberg metadata may contain sensitive file statistics. These files are not public query endpoints: every tenant must access them through S2's authorization boundary. File-storage checksums detect corruption; they are not encryption or a substitute for filesystem isolation.

Private artifact directories and tenant-scoped references protect the local layout. Path traversal, symlinks and cross-tenant descriptors are rejected. A trusted host administrator can still access plaintext files. Warehouse encryption, signed manifests and remote IAM remain production work.

The storage-report command is host-administrator-only, checks all retained references and never deletes artifacts. No network endpoint exposes warehouse paths or raw files. Backup/restore must preserve both the database and the original Iceberg warehouse path.
