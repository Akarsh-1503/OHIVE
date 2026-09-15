# ADR-0005 — Server-Sent Events for batch progress, not WebSockets or polling

**Status:** accepted · **Date:** 2026-09 · **Scope:** progress transport

## Context

A 25-card batch takes 37–50 s warm and ~250 s cold. Showing nothing for that long is not an
option: the product's credibility depends on the user seeing leads arrive *as they are
extracted*, which is also genuinely useful — you can start reviewing card 1 while card 25 is
still in the model.

The traffic shape is strictly one-directional and low-volume: a handful of events per card,
plus a coalesced progress tick. Everything the client sends — upload, edit, retry, export — is
a normal request that wants a normal response.

## Decision

**Server-Sent Events** at `GET /api/v1/batches/{id}/events`, with an automatic client-side
degradation to polling `GET /batches/{id}` after three consecutive connection failures.

## Rationale

**The transport should match the traffic.** This is a server→client stream. SSE is exactly
that, and nothing more. A WebSocket buys a bidirectional channel the product has no use for,
and charges for it in protocol machinery, heartbeat handling and a second code path for
requests that would otherwise be plain HTTP.

**It is ordinary HTTP, which matters at every layer.**
- `EventSource` is built into every browser; no client library.
- Auto-reconnect is built into the protocol, with `Last-Event-ID` available if resumption is
  ever needed.
- It traverses proxies, CDNs and corporate firewalls as normal HTTP/1.1 or HTTP/2.
- It is `curl`-able, which makes it debuggable: `curl -sSN .../events` prints the stream.
- FastAPI serves it as a `StreamingResponse` over an async generator — no extra dependency,
  no separate server, no sticky-session requirement beyond the single replica this deployment
  already has.

**Polling was rejected on both ends of the trade.** To feel live it needs a ~1 s interval,
which is 40+ requests per batch per client, each returning the full batch payload — most of
them identical. At a comfortable 5 s interval it stops feeling live. SSE sends one event when
one thing happens.

**The one configuration gotcha is known and handled.** Reverse proxies buffer responses by
default, which would hold the entire stream until the batch finished — the exact failure SSE
is meant to prevent. The deployment's Caddy config sets `flush_interval -1` on the API route,
and the API sends `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no` so an
nginx in front of it behaves too. On AWS, note that an ALB's 60 s idle timeout will cut the
stream unless raised.

## Implementation details that the choice forced

- **No gap on connect.** The subscriber's queue is registered *before* the snapshot is built,
  so an event cannot slip between the two.
- **Late subscribers still terminate.** Connecting after a batch has finished yields the
  snapshot plus a `batch.completed` synthesised from the database — which also covers the case
  where the server restarted between upload and subscription. Without this the client would
  hang forever waiting for an event that already happened.
- **Progress is coalesced** to at most one event per 200 ms, with the final one always sent. At
  6-way concurrency the raw stream is a burst of writes no UI can use.
- **Keep-alive `ping` every 15 s**, because intermediaries drop idle connections — and during a
  ~210 s cold start the stream is idle by definition.
- **Queues are unregistered on disconnect**, so an abandoned tab does not leak a subscriber.
- **Degradation is explicit, not silent.** Connection state is rendered in the UI (*Live* /
  *Reconnecting* / *Polling* / *Stream closed*). A frozen UI is a worse failure than a slow
  one, and a user who is told they are in polling mode is not confused by slower updates.

## Alternatives considered

| Option | Why not |
|---|---|
| **WebSockets** | Bidirectional capability the product does not need, plus its own reconnect, heartbeat and framing concerns. The right choice the moment a second client needs to *send* something live — collaborative review, for instance. |
| **Polling only** | 40+ redundant round-trips per batch to feel live, or a UI that does not. |
| **Long polling** | Reimplements SSE by hand, with worse ergonomics and no browser support. |
| **A hosted realtime service** (Pusher, Ably) | A third-party dependency and a second network path for a single-node demo. |
| **No live progress at all** | A 50-second blank screen, and a ~250-second one from cold. |

## Consequences

**Positive.** One dependency-free endpoint. One client hook (`use-event-source.ts`) is the only
place `EventSource` is touched. Debuggable with `curl`. Reconnection is free. The whole event
flow is covered by tests, including late and concurrent subscribers.

**Negative.**
- **The event bus is in-process**, so it is one of the two things pinning the API to a single
  replica ([ADR-0006](ADR-0006-sqlite-and-local-disk-over-postgres.md),
  [limitation #7](../07-limitations.md)). A second replica would serve clients that never
  receive events for batches processed by the first. Redis pub/sub is the swap, and the
  `EventBus` interface is drawn so that it is a swap.
- **Each connection holds a worker slot.** Fine at demo concurrency; a real deployment would
  want to bound subscribers per batch.
- **Server→client only.** Any future live client→server feature needs a different transport.
- **Buffering proxies break it silently**, which is a sharp edge for anyone deploying this
  somewhere new. It is called out in the architecture doc and in the Caddyfile comments for
  exactly that reason.
