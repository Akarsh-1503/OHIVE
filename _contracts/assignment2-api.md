# Driftless — API Contract (v1)

Frozen contract. Backend implements it; frontend consumes it.

Base URL: `/api/v1`. JSON is `snake_case`.

## Product name
**Driftless** — "Monocular video in. Metric-consistent sparse map out."
Never reference the hiring company or any internal company name anywhere in this repo.

## Coordinate convention
World frame = first keyframe camera frame. Camera looks down **+Z**, **+X** right, **+Y** down
(standard OpenCV). Poses are **camera-from-world** internally but are returned as
**world-from-camera** (`T_wc`) because that is what a renderer wants.

## Core objects

### `Job`
```jsonc
{
  "job_id": "9f2c...",
  "status": "queued|decoding|tracking|optimizing|completed|failed",
  "filename": "desk_loop.mp4",
  "created_at": "2026-09-14T09:12:03Z",
  "video": {"width": 1920, "height": 1080, "fps": 30.0, "frame_count": 300, "duration_s": 10.0},
  "metrics": { /* Metrics, null until completed */ },
  "error": null,
  "queue_position": 0,              // jobs that must finish first. 0 = running or next up.
                                    // null once the job is completed/failed.
  "truncated": false                // true when the clip was frame-capped by max_frames
}
```

Only one reconstruction runs at a time by default (`SLAM_MAX_CONCURRENT_JOBS=1`) because a
second concurrent job invalidates the published single-job benchmark. Extra jobs sit in
`status: "queued"` with a non-zero `queue_position`; the frontend should surface that number
rather than implying the job is stalled.

### `Metrics` (what we report for assignment requirement #6)
```jsonc
{
  "wall_ms": 8420,                  // upload-accepted -> reconstruction-ready, queue wait excluded
  "queue_wait_ms": 0,               // time spent in the queue before a worker picked it up
  "decode_ms": 640,
  "tracking_ms": 5310,
  "optimize_ms": 2470,
  "frames_processed": 300,
  "processing_fps": 35.6,
  "realtime_factor": 1.19,          // video_duration_s / (wall_ms/1000). >1 = faster than realtime
  "keyframes": 34,
  "map_points": 5218,
  "mean_reprojection_error_px": 0.81,
  "median_track_length": 7,
  "loop_closures": 2,
  "loop_candidates_checked": 41,
  "drift": {
    "pre_optimization_loop_error_m": 0.412,   // ||t_i - t_j|| residual across closures, before PGO
    "post_optimization_loop_error_m": 0.031,
    "reduction_pct": 92.5,
    "scale_drift_ratio": 1.07                 // Sim(3) scale correction applied at last closure
  },
  "trajectory_length_m": 6.94,      // up-to-scale units, labelled as such in UI
  "ba_runs": 34,
  "host": {"cpu": "Intel Cascade Lake @3.1GHz", "vcpu": 8, "ram_gb": 32}
}
```

### `Reconstruction`
```jsonc
{
  "job_id": "...",
  "poses": [                        // one per processed frame, ordered by frame_index
    {"frame_index": 0, "t_s": 0.0, "is_keyframe": true,
     "position": [x, y, z],
     "quaternion": [qw, qx, qy, qz],   // world-from-camera rotation
     "tracked_points": 812, "reprojection_error_px": 0.7}
  ],
  "points": {                       // flat typed arrays — keeps payload small for three.js
    "xyz": [x0,y0,z0, x1,y1,z1, ...],     // float32 flattened
    "rgb": [r0,g0,b0, ...],               // uint8 flattened, sampled from source frame
    "observations": [5, 3, 9, ...]        // per point, how many keyframes saw it
  },
  "loop_closures": [{"from_kf": 31, "to_kf": 3, "inliers": 128, "scale": 1.03}],
  "metrics": { /* Metrics */ }
}
```
Payload is gzip-compressed by the server. Points are capped at 60 000 for the web payload
(full cloud always available via the PLY export).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/jobs` | multipart `video`. Optional form fields: `max_frames` (int), `target_width` (int, default 640), `enable_loop_closure` (bool, default true). Returns `Job`, **201**. Processing starts immediately. |
| `GET` | `/jobs/{job_id}` | `Job` snapshot. |
| `GET` | `/jobs/{job_id}/events` | **SSE** progress. |
| `GET` | `/jobs/{job_id}/reconstruction` | `Reconstruction` (gzip). |
| `GET` | `/jobs/{job_id}/export/ply` | Binary PLY point cloud (full resolution). |
| `GET` | `/jobs/{job_id}/export/tum` | TUM-format trajectory `timestamp tx ty tz qx qy qz qw`. |
| `GET` | `/jobs/{job_id}/export/report.json` | Metrics + config, for the submission write-up. |
| `GET` | `/jobs/{job_id}/preview/{frame_index}` | JPEG of a processed frame with tracked features drawn. |
| `GET` | `/samples` | `[{"id","name","description","duration_s","url"}]` built-in demo clips. |
| `POST` | `/jobs/from-sample/{sample_id}` | Run a built-in clip (so reviewers can demo with zero upload). Returns `Job`, **201**. |
| `GET` | `/samples/{sample_id}/file` | The sample's media, for in-page playback. Default target of `Sample.url`. |
| `GET` | `/health` | `{"status":"ok","version":"...","workers":n,"cpu_count":n}` |

### SSE events (`/jobs/{id}/events`)
| event | data |
|---|---|
| `job.snapshot` | full `Job` |
| `job.stage` | `{"stage":"decoding\|tracking\|optimizing","message":"..."}` |
| `job.progress` | `{"frames_done":120,"frames_total":300,"fps":38.2,"keyframes":14,"map_points":2210,"loop_closures":0,"elapsed_ms":3140}` |
| `job.loop_closure` | `{"from_kf":31,"to_kf":3,"inliers":128}` |
| `job.completed` | `{"job_id":"...","metrics":{...}}` |
| `job.failed` | `{"error":"..."}` |
| `ping` | `{}` every 15 s |

`job.progress` is emitted at most every 150 ms (coalesced) so the stream never becomes the bottleneck.

## Upload rules
- `video/mp4|quicktime|x-matroska|webm|avi|x-msvideo`, max 200 MB, max 60 s.
  (`video/x-msvideo` is the spelling browsers actually send for AVI.)
- The upload is streamed to disk and must decode at least one frame before a `job_id` is
  issued; an undecodable file is rejected with `UNDECODABLE_VIDEO` and leaves nothing behind.
- Longer clips are accepted but frame-capped (`max_frames`, default 1800) and the response flags `truncated: true`.

## Error shape
All non-2xx: `{"detail": "human readable message", "code": "SNAKE_CASE_CODE"}`.

Codes emitted by this service: `UNSUPPORTED_MEDIA_TYPE`, `FILE_TOO_LARGE`, `VIDEO_TOO_LONG`,
`UNDECODABLE_VIDEO`, `EMPTY_UPLOAD`, `INVALID_PARAMETER`, `JOB_NOT_FOUND`, `JOB_NOT_COMPLETED`,
`SAMPLE_NOT_FOUND`, `FRAME_NOT_FOUND`, `ARTIFACT_NOT_FOUND`, `INTERNAL_ERROR`.

## Env vars (backend)
```
DATA_DIR=/data
SLAM_BACKEND=real                 # real | stub  (stub = synthetic reconstruction, no engine needed)
SLAM_TARGET_WIDTH=640
SLAM_MAX_FEATURES=1200
SLAM_MAX_FRAMES=1800
SLAM_WORKERS=4                    # intra-job worker threads handed to the SLAM engine
SLAM_MAX_CONCURRENT_JOBS=1        # process-pool size; jobs beyond this queue
SLAM_MAX_POINTS_WEB=60000
MAX_UPLOAD_MB=200
MAX_VIDEO_DURATION_S=60
JOB_TTL_HOURS=24
CORS_ORIGINS=*
```
