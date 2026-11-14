# Branch-aware metamodel: implemented and reserved

## Persisted entities

| Entity/table | Identity and principal fields | Invariant |
| --- | --- | --- |
| `tenants` | ID, name | Host-admin bootstrap only |
| `principals` | ID, tenant, role, clearance, compartments, active | Current authorization checked on every request |
| `tokens` | Hash, principal | Raw tokens never persisted |
| `projects` | ID, tenant, name | Tenant-scoped lookup |
| `project_memberships` | Project, principal | Explicit membership for non-admins |
| `branches` | ID, tenant, project, name, base, head | Unique name within project; expected-head publication |
| `commits` | ID, tenant, project, parents, manifest, author, timestamp | Immutable; parents preserve history |
| `revisions` | Tenant, digest, kind, canonical payload | Content-addressed, immutable |
| `dataset_transactions` | ID, actor, branch, base, operation, payload, status, result, idempotency key | Actor-owned, immutable outcome after COMMIT |
| `invocations` | ID, actor, branch, base, Action revision, edits, effect hash, status, result, idempotency key | Pinned effects; execution-time revalidation |
| `approval_decisions` | Invocation, approver, effect hash, timestamp | Distinct reviewers; current authorization required |
| `classification_overrides` | Tenant, project, resource, label | Live monotonic restrictions across all branches |
| `audit_logs` | Sequence, tenant, canonical event, previous hash, hash | Append-only API, per-tenant verification chain |

The v0.2 storage upgrade adds `storage_config` (backend and pinned absolute warehouse). File descriptors reference a tenant-scoped artifact and seal digest; application reads hydrate rows before applying current Markings. Database format 2 prevents the old application from silently reading file-backed revisions.

## Resource payloads in `revisions`

| Manifest key | Payload |
| --- | --- |
| `dataset/<name>` | Scalar schema, primary key, parent snapshot digest; either inline rows/Markings or a sealed file-storage descriptor |
| `type/<name>` | Properties, required flags, classification and interface names |
| `interface/<name>` | Required property/type/Marking contract |
| `object/<id>` | Logical identity, type, property values and object Marking |
| `linktype/<name>` | Source type, target type and Marking |
| `link/<id>` | Typed endpoints and Marking |
| `action/<name>` | Object type, allowed properties, required approvals, external effects disabled |

Object revision is the digest of the canonical object payload. Object identity is stable within a project's branch lineage. Names are currently identifiers: rename/move migration is not implemented. Production schema IDs independent of display names remain required before general schema evolution.

A snapshot does not overwrite previous bytes. A branch manifest is the joint version boundary. Approval and audit records are deliberately not included in a rollbackable manifest.

## Lifecycle

Dataset: `OPEN -> PREPARED -> COMMITTED`; OPEN/PREPARED may become ABORTED. APPEND/UPDATE/DELETE describe intent; COMMIT publishes it. A stale base fails without publication and must be aborted/restarted deliberately.

Action: `SUBMITTED -> APPROVED -> COMMITTED`; zero-quorum definitions start APPROVED. Failed validation leaves state unchanged. There is no rejection/cancellation/expiry API in this slice. Approval decisions are append-only through the application API. Production configurable queues and expiry are reserved work.

## Live policy resource selectors

`object/<id>`, `type/<name>`, `type/<name>/property/<property>`, `interface/<name>`, `interface/<name>/property/<property>`, `dataset/<name>`, `dataset/<name>/property/<property>`, `dataset/<name>/row/<canonical-JSON-primary-key>`, `link/<id>`, `linktype/<name>`.

For string key `a`, the row selector is `dataset/readings/row/"a"`. Restriction floors augment snapshot labels; they never weaken them. A rule on one branch applies project-wide.

## Reserved complete-platform entities

ResourceRevision-backed transforms, pipeline DAGs/runs, source bindings, checkpoints, application/widgets, typed Object Set query plans, SDK schema fingerprints, Function packages, external outbox intents, AI Logic/model/evaluation revisions, lineage edges, license entitlements and signed audit anchors remain planned. Corresponding subsystem boundaries exist; no implementation is claimed for them.
