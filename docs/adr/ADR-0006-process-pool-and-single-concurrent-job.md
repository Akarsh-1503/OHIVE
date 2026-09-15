# ADR-0006 — A process pool with exactly one concurrent job

**Status:** accepted · **Date:** 2026-09 · **Code:** `backend/app/jobs/store.py`

## Context

Reconstruction is CPU-bound Python. The API also has to stay responsive: serve the UI's
polling, hold open SSE connections, answer health checks, and accept new uploads — all while a
reconstruction is running.

Two independent decisions: **where** the work runs, and **how many** run at once.

## Decision 1 — a process pool, not a thread pool

SLAM in a thread would hold the GIL. OpenCV releases it inside `VideoCapture.read`, `resize`
and `ORB`, but the tracking, mapping, bundle-adjustment and pose-graph code is Python plus
small NumPy arrays — GIL-bound almost throughout. The uvicorn event loop would be starved
exactly when the user is watching progress most closely, and the SSE stream would stutter.

So every job runs in a `ProcessPoolExecutor` child. Consequences that had to be engineered:

**Progress crosses a process boundary.** The worker writes events to a
`multiprocessing.Manager` queue; a daemon thread in the API process drains it and hands each
event to the loop with `call_soon_threadsafe`. Nothing in the hot path touches the loop
directly and the loop never blocks on the worker.

**Large results stay on disk.** `run_job` returns a small summary; the reconstruction, the
PLY, the TUM file and the gzipped web payload are written by the child. Pickling a
3400-point reconstruction back across the boundary would be pure waste.

**Engine import is lazy, inside the child.** `from slam import …` happens in
`runner/adapter.py::_real_reconstruction`, not at module import. A missing or broken engine
therefore surfaces as a *failed job with an actionable message*, never as a service that will
not boot:

```
RuntimeError: SLAM engine unavailable: the `slam` package could not be imported
(No module named 'slam'). Set SLAM_BACKEND=stub to run the service without it.
```

*(That is a real captured message — the failure path was exercised accidentally during
verification, and it behaved exactly as designed: three jobs failed cleanly, the service
stayed healthy.)*

**A worker crash kills a job, not the service.** Which matters, because the worker is where an
unauthenticated attacker's video meets FFmpeg ([08 — Security](../08-security-and-cost.md)).

## Decision 2 — pool size 1

`SLAM_MAX_CONCURRENT_JOBS` defaults to **1**. This is the contested one.

### Why not more

A second concurrent reconstruction on an 8-vCPU / **4 physical core** host roughly halves
single-job throughput. Both jobs finish in roughly the time one would have taken twice over,
because they contend for the same vector units and the same memory bandwidth.

**And that would invalidate the headline claim.** Requirement #6 is "a 10-second video
processed in ≤10 seconds." With a pool of 2, that claim becomes conditional on nobody else
using the demo at the same time — which is unverifiable by a reviewer, and therefore not a
claim worth making. The published benchmark is a single-job benchmark, and the deployment is
configured to match the benchmark rather than the benchmark being configured to flatter the
deployment.

The **measured** deployment-host time is 7.0 s, 11.9 s and 12.5 s across the three bundled
clips ([06 — Performance](../06-performance.md#deployment-host--measured-and-it-does-not-meet-the-10-s-budget)) —
two of them already over budget at a pool size of one. Halving throughput would not narrow
that gap, it would roughly double it. This decision is the only reason the corridor clip is
inside the budget at all.

### What queueing looks like instead

Extra jobs sit in FIFO order with a non-zero `queue_position`:

```json
{"status": "queued", "queue_position": 1, …}
```

`queue_position` counts jobs that must finish first — `0` means running or next up, `null`
once terminal. `queue_wait_ms` is reported **separately** in the metrics and is explicitly
**excluded** from `wall_ms`, so a queued job's processing time is still comparable to an
unqueued one. Verified: a second job submitted while one was running reported
`queue_position: 1`, and its eventual `queue_wait_ms` was 1867 ms while `wall_ms` was
unaffected.

The frontend surfaces that number as text — "1 job ahead of you" — rather than showing a
spinner. A spinner during a legitimate wait looks like a hang, which is the worse failure.

### Why not autoscale, or a job queue service

Because there is one box ([ADR-0001](ADR-0001-classical-sparse-slam-over-learned-methods.md),
[01 — Architecture](../01-architecture.md)) and one concurrent user in the expected demo
traffic. Celery + Redis, or Cloud Tasks, would add two services and an operational surface to
solve a problem this demo does not have. The `queue_position` field in the contract is the
entire queueing feature, and it is four lines of arithmetic.

## Consequences

**Good:**

- The published benchmark describes what a reviewer will actually experience, because the
  deployment cannot be in a state the benchmark did not measure.
- The API stays responsive under load — SSE latency is unaffected by reconstruction CPU.
- An attacker gets a queue rather than a fork bomb: CPU consumption is capped at one
  reconstruction regardless of request rate.
- The engine is swappable at runtime (`SLAM_BACKEND=stub`) with no service restart semantics
  to reason about, because the import happens per job.

**Bad, and named:**

- **The demo serialises.** Two reviewers at once and the second waits ~5–10 s. That is the
  direct cost of the benchmark guarantee, and it is a real user-facing downside.
- **The queue is unbounded.** An attacker can enqueue arbitrarily many jobs; each one's upload
  lands on disk immediately even though processing is serial. A bounded queue returning `429`
  is the obvious fix and is not implemented.
  ([07 §12](../07-limitations.md#12-no-authentication-and-no-rate-limiting-on-the-public-demo))
- **Job state is in-process.** A restart loses the job table; artefacts survive on disk but are
  no longer addressable.
  ([07 §11](../07-limitations.md#11-single-node-single-job-no-ha))
- **Process spawn costs a few hundred milliseconds** per job on macOS (`spawn` start method).
  Measured `queue_wait_ms` on an idle host is 0–1 ms because the pool is warm, so this is
  paid once at first use rather than per job.

## Reversing it

`SLAM_MAX_CONCURRENT_JOBS=2` is a one-line environment change and requires no code. The
condition for doing it is explicit: **only after re-running the benchmark at that pool size
and republishing the numbers.** The setting is documented in `.env.example` with that
condition attached, so the trade cannot be made accidentally.
