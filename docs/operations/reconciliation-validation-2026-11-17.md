# Reconciliation validation — 17 November 2026

S2 v0.3.1 builds on user checkpoint `95abcecdb0aaa524b644f3affbc3945f432a44fc`.

## Local evidence

- Full storage-required suite: **197 tests passed**, no skips, 105.321 seconds.
- Includes the prior 164 tests and 33 new tests: 11 reconciliation scenarios on JSON, Parquet and Iceberg.
- Proprietary headers: **27 files verified**.
- Iceberg reconciliation demo: `MISSING` followed by `PRESENT`, same object ID, replay-safe retention, final reading 30, approved status `inspected`, valid audit chain with 47 events.
- No external effects. The demo database and warehouse are temporary and removed on completion.

New scenarios cover retained values and absence evidence, returning-row identity and operational edits, exact preview sets/revisions, administrator and reason requirements, stale previews, restart/retry and changed-reason rejection, missing-object authorization, live source restrictions on historical branches, atomic rollback including format upgrade, branch isolation, and preserving format 4 when a new binding is defined.

These are local functional results, not production-capacity, remote CI or RCI acceptance claims. The existing dependency deprecation and sandbox CPU-cache warnings were non-fatal; dependency versions did not change.

## Handover

Apply the update on `feature/ontology-bindings` at `95abcec`, then run the complete suite, header checker and `tooling/bindings_demo.py --storage iceberg --reconcile` on the user's machine. Review the diff before committing. No automatic push or merge is part of this package.

The first successful retention advances the catalog to format 4. Stop old processes and back up persistent catalog/warehouse pairs before using it outside the disposable demo. Failed retention rolls back the format change. Restore a pre-reconciliation backup for an application downgrade.

## Remaining work

Binding/schema migration and binding-aware branch merges remain unsupported. Retention is explicit on each missing-row build; no hard deletion or physical asset retirement is inferred. Next implement a reviewed migration contract that preserves identity, field ownership, policy and provenance across compatible schema changes.
