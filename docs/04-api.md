# 04 — API reference

Implements [`../../_contracts/assignment2-api.md`](../../_contracts/assignment2-api.md) v1.
Base path `/api/v1`, JSON is `snake_case`. Interactive OpenAPI docs at `/docs` on any running
instance.

Every request and response below was **copied from a running instance**, not written by hand.
Replace the host to run them yourself:

```bash
B=http://localhost:8000/api/v1                    # local
B=https://slam-34-47-153-95.nip.io/api/v1         # live
```

---

## Endpoint map

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness, version, pool size, which engine is loaded |
| `GET` | `/samples` | The three bundled demo clips |
| `GET` | `/samples/{id}/file` | A sample's media, for in-page playback |
| `POST` | `/jobs` | multipart upload → `201 Job` |
| `POST` | `/jobs/from-sample/{id}` | Run a bundled clip → `201 Job` (zero-upload demo) |
| `GET` | `/jobs/{id}` | `Job` snapshot |
| `GET` | `/jobs/{id}/events` | **SSE** progress stream |
| `GET` | `/jobs/{id}/reconstruction` | `Reconstruction`, gzipped |
| `GET` | `/jobs/{id}/export/ply` | Binary PLY point cloud, full resolution |
| `GET` | `/jobs/{id}/export/tum` | TUM trajectory |
| `GET` | `/jobs/{id}/export/report.json` | Metrics + config + timing definitions |
| `GET` | `/jobs/{id}/preview/{frame_index}` | JPEG of a frame with tracked features drawn |

---

## `GET /health`

```bash
curl -s $B/health
```

```json
{"status":"ok","version":"1.0.0","workers":1,"cpu_count":10,
 "active_jobs":0,"queued_jobs":0,"slam_backend":"real"}
```

`slam_backend` echoes whether the real engine or the synthetic `stub` is loaded, so a
reviewer never has to guess whether the metrics they are looking at are measured. `workers`
is the process-pool size — see [ADR-0006](adr/ADR-0006-process-pool-and-single-concurrent-job.md)
for why it is 1.

---

## `GET /samples`

```bash
curl -s $B/samples
```

```json
[
  {"id":"synthetic_loop","name":"Synthetic room loop",
   "description":"Virtual pinhole camera on a closed elliptical path inside a textured 7 m room. Returns to the start pose, so loop closure has a true match. Ships with ground-truth trajectory and cloud.",
   "duration_s":10.0,"url":"/api/v1/samples/synthetic_loop/file"},
  {"id":"synthetic_corridor","name":"Synthetic corridor (open-ended)",
   "description":"Straight 9.6 m forward translation down a textured corridor. No revisited viewpoint, so loop closure cannot fire and drift accumulates monotonically. Ships with ground truth.",
   "duration_s":10.0,"url":"/api/v1/samples/synthetic_corridor/file"},
  {"id":"desk_handheld","name":"Handheld office desk (TUM fr1/desk)",
   "description":"Real handheld 640x480 capture sweeping an office desk and returning to its starting viewpoint, with 100 Hz motion-capture ground truth. TUM RGB-D benchmark, CC BY 4.0.",
   "duration_s":10.0,"url":"/api/v1/samples/desk_handheld/file"}
]
```

> `desk_handheld` is derived from the **TUM RGB-D benchmark**, sequence
> `rgbd_dataset_freiburg1_desk`, Computer Vision Group, Technical University of Munich
> (J. Sturm, N. Engelhard, F. Endres, W. Burgard, D. Cremers, *A Benchmark for the Evaluation
> of RGB-D SLAM Systems*, IROS 2012), redistributed under **CC BY 4.0**. Full attribution in
> [`../samples/README.md`](../samples/README.md).

This endpoint reads `SAMPLES_DIR`. The API image ships that directory **empty**; the compose
file mounts `./samples:/samples:ro` into it. Without that mount this returns `[]`.

---

## `POST /jobs`

Multipart. Field `video` is required; `max_frames`, `target_width` and
`enable_loop_closure` are optional form fields.

```bash
curl -s -X POST \
  -F "video=@samples/synthetic_corridor.mp4;type=video/mp4" \
  -F "enable_loop_closure=false" \
  $B/jobs
```

```json
{
  "job_id": "5e163be5cd3844c18f2ad6eb7907a72b",
  "status": "queued",
  "filename": "synthetic_corridor.mp4",
  "created_at": "2026-09-15T09:44:17Z",
  "video": {"width": 1280, "height": 720, "fps": 30.0,
            "frame_count": 300, "duration_s": 10.0},
  "metrics": null,
  "error": null,
  "queue_position": 0,
  "truncated": false
}
```

**201**, and processing starts immediately. The `video` object is already populated because
admission decodes a frame before issuing the id.

Admission rules, all enforced **before** a `job_id` exists:

| rule | code on failure |
|---|---|
| content type in `video/mp4, quicktime, x-matroska, webm, avi, x-msvideo` | `UNSUPPORTED_MEDIA_TYPE` (415) |
| body non-empty | `EMPTY_UPLOAD` (400) |
| ≤ `MAX_UPLOAD_MB` (200) | `FILE_TOO_LARGE` (413) |
| ≤ `MAX_VIDEO_DURATION_S` (60) | `VIDEO_TOO_LONG` (400) |
| `64 ≤ target_width ≤ 4096`, `max_frames ≥ 1` | `INVALID_PARAMETER` (400) |
| at least one frame decodes | `UNDECODABLE_VIDEO` (400) |

`video/x-msvideo` is in the allowlist because that is the spelling browsers actually send for
AVI; `video/avi` is accepted too.

Clips longer than `max_frames` (default 1800) are accepted, frame-capped, and flagged
`truncated: true`.

---

## `POST /jobs/from-sample/{sample_id}`

The zero-upload path, and the fastest way for a reviewer to see the product work.

```bash
curl -s -X POST $B/jobs/from-sample/synthetic_loop
```

```json
{"job_id":"54b9492ac5bc421fa29febeb4ce5b206","status":"queued",
 "filename":"synthetic_loop.mp4","created_at":"2026-09-15T05:06:20Z",
 "video":{"width":1280,"height":720,"fps":30.0,"frame_count":300,"duration_s":10.0},
 "metrics":null,"error":null,"queue_position":0,"truncated":false}
```

### Queueing

`SLAM_MAX_CONCURRENT_JOBS` is 1. Submit a second job while one is running and it queues:

```bash
curl -s -X POST $B/jobs/from-sample/synthetic_corridor
```

```json
{"status":"queued","queue_position":1, …}
```

`queue_position` counts jobs that must finish first: `0` means running or next up, and it
becomes `null` once the job is terminal. The UI surfaces that number rather than implying the
job has stalled. Rationale: [ADR-0006](adr/ADR-0006-process-pool-and-single-concurrent-job.md).

---

## `GET /jobs/{job_id}`

```bash
curl -s $B/jobs/$JOB
```

```json
{
  "job_id": "54b9492ac5bc421fa29febeb4ce5b206",
  "status": "completed",
  "filename": "synthetic_loop.mp4",
  "created_at": "2026-09-15T05:06:20Z",
  "video": {"width":1280,"height":720,"fps":30.0,"frame_count":300,"duration_s":10.0},
  "metrics": {
    "wall_ms": 8929,
    "queue_wait_ms": 0,
    "decode_ms": 673,
    "tracking_ms": 4076,
    "optimize_ms": 3666,
    "frames_processed": 300,
    "processing_fps": 33.6,
    "realtime_factor": 1.12,
    "keyframes": 38,
    "map_points": 3434,
    "mean_reprojection_error_px": 1.3619,
    "median_track_length": 2.0,
    "loop_closures": 1,
    "loop_candidates_checked": 41,
    "drift": {
      "pre_optimization_loop_error_m": 106.941189,
      "post_optimization_loop_error_m": 0.001206,
      "reduction_pct": 99.999,
      "scale_drift_ratio": 18.538061
    },
    "trajectory_length_m": 341.821,
    "ba_runs": 38,
    "host": {"cpu": "aarch64", "vcpu": 10, "ram_gb": 8}
  },
  "error": null,
  "queue_position": null,
  "truncated": false
}
```

> **That response is real, and it is a bad run — deliberately left in.** It was captured
> inside Docker Desktop's emulated arm64 Linux VM, where a different OpenCV build takes
> different RANSAC and SVD paths and this clip hits the low-parallax scale-inflation failure
> mode: `trajectory_length_m` 341.8 against a ground truth of 8.85, `scale_drift_ratio`
> 18.5. Loop closure then rescales the whole map, which is why `reduction_pct` reads 99.999.
> The same clip on the native benchmark host produces `scale_drift_ratio` **0.9986** and
> `trajectory_length_m` **8.9**. Both numbers are honest; the discrepancy is
> [named as a limitation](07-limitations.md#8-platform-dependent-numerics) rather than
> papered over by quoting only the good one. All published figures are the native runs in
> [`../bench/results.json`](../bench/results.json).

`realtime_factor` is `video_duration_s / (wall_ms/1000)`; above 1 means faster than realtime.
`trajectory_length_m` and every other length are **up to scale**, and the UI labels them so.

---

## `GET /jobs/{job_id}/events` — SSE

```bash
curl -sN $B/jobs/$JOB/events
```

Real stream, trimmed:

```
event: job.snapshot
data: {"job_id":"39bbbacaf8374801b2ad0d27f0ceee05","status":"tracking","filename":"synthetic_corridor.mp4","created_at":"2026-09-15T05:06:58Z","video":{"width":1280,"height":720,"fps":30.0,"frame_count":300,"duration_s":10.0},"metrics":null,"error":null,"queue_position":0,"truncated":false}

event: job.progress
data: {"frames_done":235,"frames_total":300,"fps":56.05,"keyframes":20,"map_points":3512,"loop_closures":0,"elapsed_ms":4193}

event: job.progress
data: {"frames_done":253,"frames_total":300,"fps":57.01,"keyframes":21,"map_points":3736,"loop_closures":0,"elapsed_ms":4438}

event: job.stage
data: {"stage":"optimizing","message":"final optimisation"}

event: job.completed
data: {"job_id":"39bbbacaf8374801b2ad0d27f0ceee05","metrics":{"wall_ms":5096,"queue_wait_ms":0,"decode_ms":614,"tracking_ms":3156,"optimize_ms":1860,"frames_processed":300,"keyframes":24,"map_points":4466,"loop_closures":0,"loop_candidates_checked":4,"ba_runs":23,"mean_reprojection_error_px":0.5064,"median_track_length":2.0,"trajectory_length_m":31.5055,"drift":{"pre_optimization_loop_error_m":0.0,"post_optimization_loop_error_m":0.0,"reduction_pct":0.0,"scale_drift_ratio":1.0},"host":{"cpu":"aarch64","vcpu":10,"ram_gb":8},"processing_fps":58.87,"realtime_factor":1.962}}
```

| event | data |
|---|---|
| `job.snapshot` | the full `Job`, sent immediately on subscribe |
| `job.stage` | `{"stage":"decoding\|tracking\|optimizing","message":"…"}` |
| `job.progress` | `{"frames_done","frames_total","fps","keyframes","map_points","loop_closures","elapsed_ms"}` |
| `job.loop_closure` | `{"from_kf","to_kf","inliers"}` — emitted the moment a closure is accepted |
| `job.completed` | `{"job_id","metrics"}` |
| `job.failed` | `{"error":"…"}` |
| `ping` | `{}` every 15 s, to keep proxies from idling the connection out |

Three behaviours worth knowing:

- **`job.progress` is coalesced to at most one per 150 ms.** At 60–110 fps the engine would
  otherwise emit an event every 9–16 ms and the stream would become the bottleneck it is
  reporting on. Note the `frames_done` jumps in the trace above: 235 → 253 → 268.
- **Late subscribers do not miss the ending.** Subscribing to an already-finished job
  replays the terminal `job.completed` / `job.failed` and then closes — the frontend does not
  need a separate "did I miss it?" path.
- **Caddy sets `flush_interval -1` on this route.** Without it the proxy buffers and the
  whole stream arrives at once, at the end.

The frontend consumes this through one hook (`lib/use-event-source.ts`) with exponential
backoff and a polling fallback to `GET /jobs/{id}` after three consecutive failures.
Rationale for SSE over WebSockets: the stream is strictly one-directional.

---

## `GET /jobs/{job_id}/reconstruction`

Gzipped, served pre-compressed from disk. Points are flat typed arrays, not an object per
point — see [ADR-0007](adr/ADR-0007-flat-typed-arrays-over-object-per-point-json.md).

```bash
curl -s --compressed $B/jobs/$JOB/reconstruction | head -c 400
```

```jsonc
{
  "job_id": "54b9492ac5bc421fa29febeb4ce5b206",
  "poses": [
    {"frame_index": 3, "t_s": 0.1, "is_keyframe": true,
     "position": [0.9079992321465101, -0.1474636451927129, 0.39216306276434254],
     "quaternion": [0.9992654136670961, -0.0013707775149026085,
                    -0.038072273605004474, 0.004154034217201737],
     "tracked_points": 298, "reprojection_error_px": 0.2565}
    // … one per successfully tracked frame, ordered by frame_index
  ],
  "points": {
    "xyz": [4.0328, 3.9374, 25.3004, 2.2679, 3.9041, 25.377, …],  // float32, flattened
    "rgb": [33, 31, 32, 34, 35, 30, …],                           // uint8, flattened
    "observations": [3, 5, 5, 5, 5, 4, …]     // per point, how many keyframes saw it
  },
  "loop_closures": [{"from_kf": 37, "to_kf": 9, "inliers": 167, "scale": 0.998609}],
  "metrics": { /* the Metrics object above */ }
}
```

*(values above are from a native run of `synthetic_loop`: 296 poses, 3431 points, one accepted
closure from keyframe 37 back to keyframe 9 with 167 Sim(3) inliers.)*

Note that `poses` has **296 entries for a 300-frame clip**, not 300. One pose is emitted per
*successfully tracked* frame; frames the tracker could not place are omitted rather than
back-filled with a guess. On the real handheld clip that gap is much larger and is the first
item in [07 — Limitations](07-limitations.md).

Measured size on `synthetic_loop` (3434 points, 300 poses): **336 kB** uncompressed, **122 kB**
gzipped on disk, and the wire response is 316 kB when the client does not send
`Accept-Encoding: gzip`.

Points are capped at `SLAM_MAX_POINTS_WEB` (60 000). The subsample is **deterministic and
quality-aware**, not random: ranked by observation count descending, ties broken by per-point
reprojection error ascending, with the last 15 % of the budget filled by a uniform stride over
what remains. A pure top-N silently deletes whole regions of the map that only a handful of
keyframes saw — typically the end of the trajectory. The full cloud is always available
through the PLY export.

`position` is the camera centre in world coordinates and `quaternion` is
`[qw, qx, qy, qz]` of the **world-from-camera** rotation. Conventions:
[01 — Architecture](01-architecture.md#coordinate-conventions).

---

## Exports

### `GET /jobs/{id}/export/ply`

Binary little-endian PLY, **full** cloud, `float x y z` + `uchar red green blue`.

```bash
curl -sO -J $B/jobs/$JOB/export/ply
head -c 200 cloud.ply
```

```
ply
format binary_little_endian 1.0
comment Driftless monocular SLAM, reconstruction is up to scale
element vertex 3434
property float x
property float y
property float z
property uchar red
property uchar green
property uchar blue
end_header
```

The `comment` line is deliberate: the file is going to end up in a mesh viewer detached from
this documentation, and it should still say that its units are arbitrary.

### `GET /jobs/{id}/export/tum`

```bash
curl -s $B/jobs/$JOB/export/tum | head -3
```

```
# timestamp tx ty tz qx qy qz qw
0 0 0 0 0 0 0 1
0.133333333 0.886327786 -0.156588428 0.505073254 -0.00103233516 -0.048976562 0.0103748282 0.99874551
```

Scalar-last quaternions, as the TUM format requires. This means a reviewer can score the
trajectory with the standard tool against the shipped ground truth, with none of our code in
the loop:

```bash
evo_ape tum samples/synthetic_loop_gt_tum.txt trajectory.txt -va
```

### `GET /jobs/{id}/export/report.json`

Everything needed to write up a run: the job, the effective config, the metrics, and the
**definitions of the timing fields**.

```bash
curl -s $B/jobs/$JOB/export/report.json
```

```json
{
  "job_id": "8734bd318af74b58bd32360a7d483d66",
  "filename": "desk_handheld_tum.mp4",
  "created_at": "2026-09-15T09:44:17Z",
  "status": "completed",
  "truncated": false,
  "video": {"width":640,"height":480,"fps":30.0,"frame_count":300,"duration_s":10.0},
  "config": {"target_width":640,"max_features":1200,"enable_loop_closure":true,
             "max_frames":1800,"workers":4,"backend":"stub","max_points_web":60000},
  "metrics": { … },
  "reconstruction": {"points_total":6000,"points_web":6000,"poses":300,"loop_closures":2},
  "timing": {"wall_ms":1367,"queue_wait_ms":1867,
             "payload_emit_ms":46,"accept_to_bytes_on_disk_ms":1413},
  "timing_definition": {"wall_ms":"…","queue_wait_ms":"…","payload_emit_ms":"…"}
}
```

*(captured on the stub backend — note `"backend":"stub"` — which is why the wall time is
1.4 s. The field shapes are identical for the real engine.)*

`timing_definition` is the authoritative, machine-readable statement of what `wall_ms`
includes and excludes. It is quoted verbatim in
[06 — Performance](06-performance.md#what-wall_ms-means). Shipping it inside the artefact
means the number can never drift away from its own definition.

`accept_to_bytes_on_disk_ms = wall_ms + payload_emit_ms` is provided so the exclusion is
**auditable rather than asserted**.

### `GET /jobs/{id}/preview/{frame_index}`

JPEG of a processed frame with tracked features drawn, annotated with the tracked-point count
and reprojection error the engine reported for that frame.

```bash
curl -sI $B/jobs/$JOB/preview/120 | head -3
```

```
HTTP/1.1 200 OK
content-type: image/jpeg
content-length: 157022
```

Honest caveat, also in the backend README: this **re-detects ORB** on the requested frame
rather than reprojecting map points, because the contracted `Reconstruction` carries neither
per-frame keypoints nor intrinsics. It shows what the detector saw, not what the tracker
matched.

---

## Errors

Every non-2xx response is `{"detail": "...", "code": "SNAKE_CASE_CODE"}`. Real examples:

```bash
curl -s -X POST -F "video=@notes.txt;type=text/plain" $B/jobs
```
```json
{"detail":"content type 'text/plain' is not an accepted video type (video/avi, video/mp4, video/quicktime, video/webm, video/x-matroska, video/x-msvideo)","code":"UNSUPPORTED_MEDIA_TYPE"}
```

```bash
curl -s -X POST -F "video=@notes.txt;type=video/mp4" -F "target_width=10" $B/jobs
```
```json
{"detail":"target_width must be in [64, 4096]","code":"INVALID_PARAMETER"}
```

```bash
curl -s -X POST -F "video=@notes.txt;type=video/mp4" $B/jobs
```
```json
{"detail":"video could not be decoded: file could not be opened as video","code":"UNDECODABLE_VIDEO"}
```

```bash
curl -s $B/jobs/deadbeef
```
```json
{"detail":"no job with id deadbeef","code":"JOB_NOT_FOUND"}
```

```bash
curl -s -X POST $B/jobs/from-sample/nope
```
```json
{"detail":"no sample with id nope","code":"SAMPLE_NOT_FOUND"}
```

```bash
curl -s $B/jobs/$FRESH_JOB/reconstruction
```
```json
{"detail":"job 8734bd318af74b58bd32360a7d483d66 is queued, not completed","code":"JOB_NOT_COMPLETED"}
```

Full code list: `UNSUPPORTED_MEDIA_TYPE`, `FILE_TOO_LARGE`, `VIDEO_TOO_LONG`,
`UNDECODABLE_VIDEO`, `EMPTY_UPLOAD`, `INVALID_PARAMETER`, `JOB_NOT_FOUND`,
`JOB_NOT_COMPLETED`, `SAMPLE_NOT_FOUND`, `FRAME_NOT_FOUND`, `ARTIFACT_NOT_FOUND`,
`INTERNAL_ERROR`.

Note what the `UNDECODABLE_VIDEO` case did **not** leave behind: no job id, no directory, no
artefact for the janitor to reap. Admission either produces a complete job or nothing.

---

## Cross-cutting

**Request IDs.** Every response carries `X-Request-ID` (echoed from the request if supplied,
otherwise generated), and every structured log line for that request carries the same value.
SSE routes are exempt from completion logging, because logging the duration of a long-lived
stream is noise.

**CORS.** `CORS_ORIGINS` defaults to `*`, which is correct for the cross-origin developer
stack (`:3000` → `:8000`). Behind the production reverse proxy both tiers are same-origin and
this can be tightened — see [08 — Security & cost](08-security-and-cost.md).

**No blanket gzip middleware.** The only response big enough to matter is `/reconstruction`,
which is gzipped once at write time and served pre-compressed. Blanket middleware would
instead buffer whole sample videos into memory and burn CPU re-compressing JPEG previews.

**Models forbid unknown fields.** The pydantic models use `extra="forbid"`, so a response that
drifts from the contract fails in tests rather than silently in a browser.
