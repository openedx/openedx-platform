# Durable library-wide indexing slice

## Baseline and boundary

Base: `7944dc908dffff5ff21754859fc0ca554005b313` of openedx/edx-platform.
Content search already has Celery tasks. Library create and rename handlers call their library-wide task with `.apply`, which executes synchronously. Some source library events are themselves emitted by workers; this change decouples that event worker from the engine wait, rather than asserting every event originates in an HTTP publication request.

## Implemented contract

`LIBRARY_SEARCH_ASYNC_INDEXING=True` opts only library create/rename into a durable database intent. Default behavior stays compatible with the current frontend. SearchAccess creation remains immediate. Library block, container, collection, association and course flows retain their existing behavior.

Within the caller's transaction, request_library_index increments a per-library requested revision. A post-commit callback dispatches a Celery task. Broker operational failures are logged; the database request survives. A rolled-back caller transaction leaves neither intent nor dispatch.

The worker takes the library request row lock, reads current authoritative content rather than an old event payload, streams block, collection and container documents through the existing document constructors and `_update_index_docs` API boundary (including their breadcrumbs), then advances completion only after every batch's engine task wait returns. Source querysets use `iterator(chunk_size=batch_size)` rather than populating Django's full result cache. The default batch size is 100 documents, configurable with the positive integer `LIBRARY_SEARCH_INDEX_BATCH_SIZE`. The synchronous baseline API remains unchanged. Each batch retains the existing current-index wait and concurrent-rebuild dual-write behavior; the existing rebuild shadow write is not newly given a completion wait. Duplicate deliveries are no-ops; multiple pending requests coalesce. Failures roll back completion. Celery retries connection/Meilisearch errors five times with backoff and jitter, and late acknowledgment plus worker-loss rejection allow redelivery. A bounded recovery command redispatches unresolved rows, including requests lost between database commit and broker dispatch. Run it periodically through the deployment's existing scheduler; no scheduler is silently installed.

Library deletion cancels the durable row. A missing-library exception during delivery also removes the request, avoiding a permanent poison item. This cancellation does not introduce deletion of existing search documents; library lifecycle cleanup remains the existing upstream responsibility.

## Lock and ordering limits

The row lock spans the engine call deliberately: queued library-wide writes cannot overtake one another, and enqueue revisions cannot be acknowledged accidentally by an older worker. A crash after an engine write but before the database commit causes replay of current content, giving idempotent convergence rather than exactly-once delivery.

This conservative design means a subsequent create/rename enqueue for the same library can wait for an active worker's engine call. It moves the ordinary wait out of the event path but does not guarantee bounded authoring latency under lock contention. Pending revisions are ordered by transaction arrival; they are not source content versions. They always reread current content, so delayed notifications converge on current state. Existing per-item writes do not acquire this lock: their concurrent changes can race library-wide indexing. Therefore global stale-write prevention is not claimed.

SQLite does not enforce select_for_update. The isolated tests prove persistence/rollback/coalescing, not production row-lock ordering. Before activation, run concurrent-worker/rename/deletion fault tests against the supported production database and the real engine. For nonblocking enqueue, a future immutable-intent table with a separate per-library worker lock is preferable; it needs explicit FK-lock and deletion semantics on the deployment database.

## Rollout and outstanding work

1. Obtain maintainer agreement for the bounded scope and setting name; run CMS integration tests and migration checks with pinned dependencies.
2. Deploy the additive migration with the setting false. Deploy workers before enabling the setting.
3. Configure periodic bounded recovery and alert on age/revision lag; validate engine timeout and broker retry settings.
4. Add an author-visible pending/failed indicator and refetch behavior before opt-in production use. This slice exposes durable internal state, not a new public status endpoint or frontend.
5. Run production-database concurrency tests and a real Meilisearch fault-injection scenario. Redesign the long lock or unify per-item writes before claiming comprehensive order safety.

Document batches are bounded, but document byte size, nested metadata construction, and database-driver buffering are not. End-to-end process memory therefore requires measurement on the production database. Engine failure after an accepted batch leaves the revision pending; recovery rebuilds batches from current content and replays accepted earlier batches. Library disappearance after a partial write cancels the request, but does not clean up partially indexed documents. Library deletion/reconciliation must handle that existing lifecycle gap.

Per-item durable intents, source versions, cross-engine support, telemetry, job dead-letter UI, library-wide deletion repair, retention cleanup, and deployment benchmarks remain open. This is a concrete initial contribution, not completion of the unavailable 39-page design package.

Disabling the setting restores synchronous future events but does not cancel already pending requests. Drain/recover pending rows before rollback; workers can continue draining with the setting false. Do not drop the table with pending work.
