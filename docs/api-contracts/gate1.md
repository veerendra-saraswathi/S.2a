# Gate 1 development API

The loopback API is `POST /v1/operations`. Required header: `Authorization: Bearer <token>`. Body: `{"operation": "<name>", "params": {...}}`. No caller-supplied tenant or principal is accepted. Bootstrap is CLI-only.

Successful operations return HTTP 200 and a JSON object. Errors use 400 (invalid request), 401 (missing HTTP credential), 403 (authorization), 404 (unknown or hidden resource), 409 (stale/conflicting state), 413 (request cap), or 503 (storage unavailable). Hidden object reads return 404. This is a development adapter, not the final public REST contract.

| Operation | Required role | Parameters |
| --- | --- | --- |
| `actions.approve` | admin | branch, invocation, effect_hash |
| `actions.define` | write | branch, expected_head, name, object_type, writable_properties, approvals (optional) |
| `actions.execute` | write | branch, invocation |
| `actions.submit` | write | branch, expected_head, action, edits, idempotency_key |
| `audit.verify` | admin | None |
| `branches.fork` | write | branch, name |
| `branches.head` | read | branch |
| `branches.merge` | admin | branch, source, expected_head, expected_source |
| `datasets.abort` | write | branch, transaction |
| `datasets.begin` | write | branch, expected_head, dataset, operation, idempotency_key |
| `datasets.commit` | write | branch, transaction |
| `datasets.create` | write | branch, expected_head, dataset, schema, primary_key |
| `datasets.read` | read | branch, dataset |
| `datasets.stage` | write | branch, transaction, rows |
| `links.list` | read | branch |
| `links.put` | write | branch, expected_head, link_id, link_type, source, target, label (optional) |
| `objects.get` | read | branch, object_id |
| `objects.list` | read | branch, object_type (optional) |
| `objects.put` | write | branch, expected_head, object_id, object_type, values, label (optional), expected_revision (optional) |
| `ontology.define` | write | branch, expected_head, name, definition |
| `ontology.interface` | write | branch, expected_head, name, definition |
| `ontology.link_type` | write | branch, expected_head, name, source_type, target_type, label (optional) |
| `policy.restrict` | admin | branch, resource, label |
| `principals.create` | admin | role, clearance (optional), compartments (optional) |
| `principals.revoke` | admin | principal |
| `projects.create` | admin | name |
| `projects.grant` | admin | project, principal |

Role `read` includes viewer/editor/admin; `write` includes editor/admin. Project membership and Markings are additional checks. Admin does not override Markings.

## Dataset sequence

1. `datasets.create`: supply `branch`, `expected_head`, `dataset`, `schema` and a required scalar `primary_key`.
2. `datasets.begin`: operation APPEND/UPDATE/DELETE and a unique idempotency key.
3. `datasets.stage`: use transaction ID and a row list. Rows are `{"values":{"id":"a","value":1},"marking":{"level":0,"compartments":[]}}`. UPDATE supplies a complete replacement row. DELETE supplies only the primary key inside values.
4. `datasets.commit`: publishes the prepared transaction. The same actor may retry COMMIT safely.

## Action sequence

Define allowed properties, submit edits with expected object revisions, approve if required, then execute using the submitting principal. Each edit is `{"object":"pump-01","expected_revision":"<digest>","set":{"status":"inspected"}}`.

A stale branch head invalidates execution. Refreshing an approval is not enough: resubmit the reviewed effect against current state. The Action idempotency key is scoped to tenant and branch and bound to actor, input and base.

## Schema example

```json
{"properties":{"id":{"type":"string","required":true},"value":{"type":"integer","required":true}}}
```

Supported scalar types: string, integer, number and boolean. Required fields cannot be omitted. Null is not a supported scalar value in this slice. Integers exclude booleans. Unknown object/row properties are rejected.

## Concurrency and retry

Mutations requiring expected_head must use the current branch commit. Object replacement additionally requires expected_revision; omission means create-only. Dataset begin/commit and Action submit/execute have the documented idempotency semantics. Do not assume that project creation or every API operation is retry-idempotent.

The JSON envelope schema documents operation names, required fields and unknown-field rejection. It intentionally does not encode all domain validation; executable schema, authorization and transaction checks are authoritative.

## Reviewed branch merge (v0.3.3)

`branches.merge_preview` requires admin access and `branch`, `source`, `expected_head`, `expected_source`, `reason`. It returns pinned commits, resource revision changes and a `plan_hash`. `branches.merge_reviewed` takes those same parameters plus `plan_hash` and `idempotency_key`. Reuse the exact request on retry. Both heads and current effective policies are checked before initial publication. Replays recheck current access. See [scope, errors and limits](../architecture/reviewed-merges.md).

## File intake (v0.4.0)

`ingestion.preview`, `ingestion.apply` and `ingestion.get` are documented in [file ingestion](../architecture/file-ingestion.md), including exact parameters, classifications, replay and limits.
