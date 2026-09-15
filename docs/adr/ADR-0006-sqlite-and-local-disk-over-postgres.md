# ADR-0006 — SQLite and local disk, not Postgres and object storage

**Status:** accepted · **Date:** 2026-09 · **Scope:** persistence

## Context

The service needs to persist, per batch: a few dozen lead rows, the uploaded card images, the
model's raw JSON payload per card (for the audit sheet in the export), and derived thumbnails.

The actual scale, stated plainly rather than assumed:

| | |
|---|---|
| Rows per batch | ≤ 25 |
| Batch size on disk | ~2–30 MB of images |
| Write pattern | one row update per card, ~6 concurrent |
| Read pattern | point lookups by primary key, plus one ordered scan per batch |
| Concurrent users | one, realistically; the demo is single-tenant |
| Query complexity | no joins beyond `cards.batch_id`, no analytics, no full-text search |

Every statement in `store.py` is a sub-millisecond point read or write.

## Decision

**`sqlite3` from the standard library**, one file in `DATA_DIR`, WAL journal mode, one shared
connection guarded by a lock. Card images and thumbnails as **plain files** on the same
volume.

## Rationale

**It is the right *size* of tool.** A batch is a few dozen rows that must survive a restart.
Postgres would add a second container, a connection pool, a migration tool, a health
dependency in the compose file and a backup story — to serve queries that fit in a single
index lookup.

**It keeps `docker compose up` to two services.** That is not a cosmetic point: the quickstart
promise is *clone, one command, working product, no credentials*. Every service added to that
command is a thing that can fail on a reviewer's laptop for reasons that have nothing to do
with this project.

**WAL mode is what makes it safe here.** Readers do not block the writer and the writer does
not block readers, which is exactly the access pattern of "six workers updating their own rows
while an SSE stream reads the batch". `synchronous=NORMAL` is the right durability point for a
demo: a power loss could lose the last transaction, and the retry endpoint exists.

**One connection, one lock, shared across threads** (`check_same_thread=False`). With
sub-millisecond statements, lock contention is not measurable, and it removes an entire class
of pool-configuration bug.

**Images belong on a filesystem, not in a database.** They are written once, read by id, and
served with a cache header. A BLOB column would put multi-megabyte binaries through the same
transaction log as 200-byte row updates for no benefit.

**The audit trail is cheap.** Storing the model's raw JSON per card costs a TEXT column and
buys the export's Raw sheet — every value on the Leads sheet can be traced back to what the
model actually said. In a Postgres + S3 design that is another bucket and another lifecycle
policy.

## Alternatives considered

| Option | Why not |
|---|---|
| **Postgres + S3/GCS** | The correct choice for multi-replica, multi-tenant, retained-data production. Here: three more moving parts, credentials in the quickstart, and no capability this workload needs. |
| **Postgres only, images as BLOBs** | All of the above, plus binaries in the WAL. |
| **In-memory only** | A restart loses every batch mid-review. The review queue is the product; it has to survive `docker compose restart`. |
| **A document store** (Mongo, DynamoDB) | The data is relational (a batch has cards) and tiny. Nothing gained. |
| **Files only, no database** | Needs hand-rolled indexing and atomic updates — i.e. a worse SQLite. |

## Consequences

**Positive.** Zero operational surface: no server, no pool, no migration tool, no credentials.
The whole test suite runs against a temporary directory with no fixtures beyond `mkdtemp`.
Backup is `tar` of one directory. The database file is inspectable with `sqlite3` over an SSH
tunnel when something looks wrong.

**Negative — and this is the honest cost.**
- **It pins the API to a single replica.** SQLite on one file could tolerate more, but the
  card images on local disk cannot: a second replica cannot serve an image it does not have.
  Together with the in-process SSE bus ([ADR-0005](ADR-0005-sse-over-websockets-and-polling.md))
  this is the reason there is no horizontal scaling story today.
- **No HA.** One node, one disk, no replication, **no backups configured**. If the volume goes,
  the batches go.
- **No retention or deletion machinery.** There is no `DELETE /batches/{id}` and no TTL sweep,
  which is a compliance problem for a product that ingests other people's contact details —
  [limitation #8](../07-limitations.md). Adding it is straightforward precisely *because* the
  data is one directory and one table.
- **Writes serialise behind one lock.** Invisible at 25 rows per batch; it would not be at
  25 000.

**The exit is deliberately cheap.** `store.py` is the only module that knows how anything is
persisted — nothing in `routes/`, `extract/` or `vlm/` imports `sqlite3`, and the pipeline
talks to a `Store` object rather than to SQL. Moving to Postgres plus object storage is a
rewrite of one module and a swap of `EventBus` for Redis pub/sub. The boundary was drawn for
that migration; the migration was not done, because at this scale it would be cost without
benefit.
