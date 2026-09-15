# Driftless — backend (`slam-api`)

Monocular video in. Metric-consistent sparse map out.

FastAPI service around the `slam` engine. Implements `_contracts/assignment2-api.md` v1 under
`/api/v1`. No service layer:

| module | responsibility |
|---|---|
| `app/main.py` | app factory, middleware, lifespan, CORS, error handlers |
| `app/models.py` | pydantic v2 models mirroring the contract (`extra="forbid"`) |
| `app/routes/` | `deps` (accessors + `ApiError`), `jobs`, `events` (SSE), `artifacts`, `samples`, `health` |
| `app/jobs/` | `settings` (env parsing), `store` (job store, process pool, event bus, janitor) |
| `app/runner/` | `adapter` (the `slam` package + worker entrypoint), `stub` (synthetic backend) |
| `app/exports.py` | PLY / TUM / report.json, the web point budget, frame previews |

## Quickstart

```bash
make dev                     # http://localhost:8000/api/v1/health, stub engine
make test                    # pytest, offline, stub engine
make lint                    # ruff check
make build                   # docker build (context is assignment2/, one level up)
```

The container:

```bash
docker build -f backend/Dockerfile -t slam-api assignment2/
docker run -p 8000:8000 -v driftless-data:/data -e SLAM_BACKEND=stub slam-api
```

The build context is `assignment2/`, not `assignment2/backend/`, because the image installs
the sibling `slam/` package. If `slam/` has no installable package the build still succeeds
and the image is stub-only.

## SLAM backends

`SLAM_BACKEND=real` (default) imports `slam` lazily *inside the worker process*. A missing or
broken engine surfaces as a failed job with an actionable error, never as a dead service.

`SLAM_BACKEND=stub` generates a synthetic reconstruction — camera on a rising helix, ~6 000
points on a cylindrical shell and floor plane, colours sampled from the real clip, contract-
shaped metrics and realistically paced progress events. The entire test suite runs on it with
zero dependency on the engine.

## Concurrency

Each job runs in a `ProcessPoolExecutor` child, so the uvicorn loop never contends with SLAM
for the GIL. The pool defaults to **one** worker (`SLAM_MAX_CONCURRENT_JOBS`): a second
concurrent reconstruction on an 8-vCPU host roughly halves single-job throughput and would
invalidate the published benchmark. Extra jobs queue in FIFO order and report `queue_position`
in their `Job` payload. Progress crosses the process boundary over a `multiprocessing.Manager`
queue drained by a daemon thread that hands events to the loop via `call_soon_threadsafe`.

## What `wall_ms` means

See `GET /jobs/{id}/export/report.json` → `timing_definition`. Short version: it is the
monotonic span from upload-acceptance (clip on disk, proved decodable, job id issued) to
reconstruction-ready (poses and points computed, PLY and TUM written, web cloud subsampled),
minus `queue_wait_ms`. It **includes** pool dispatch, decode, tracking, loop-closure search,
optimisation and export serialisation. It **excludes** the client's upload time on the wire,
the admission decode probe, time queued behind another job, and the terminal JSON+gzip emit —
which is measured separately as `timing.payload_emit_ms` so the exclusion is auditable rather
than asserted.

## Environment

Every variable is documented in `.env.example`. The ones that change behaviour rather than
tuning: `SLAM_BACKEND`, `SLAM_MAX_CONCURRENT_JOBS`, `SLAM_MAX_POINTS_WEB`, `JOB_TTL_HOURS`,
`DATA_DIR`, `SAMPLES_DIR`.

## Limitations

- Job state is in-process. A restart loses the job table; the artifacts survive on disk but
  are no longer addressable. Single-node by design — this is a demo service, not a cluster.
- `/preview/{frame_index}` re-detects ORB on the requested frame rather than reprojecting map
  points, because the contracted `Reconstruction` carries neither per-frame keypoints nor
  camera intrinsics. The overlay is annotated with the tracked-point count and reprojection
  error the engine actually reported for that frame.
- The janitor is time-based only; there is no disk-pressure trigger.
