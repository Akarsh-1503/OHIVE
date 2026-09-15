"""Artifact writers and readers: PLY, TUM, report.json, web point budget, frame previews.

The SLAM engine is allowed to write PLY/TUM itself (`recon.to_ply` / `recon.to_tum`); these
writers are the fallback used by the stub backend and by any engine build that omits them, so
the on-disk byte format is identical either way.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

PLY_HEADER = (
    "ply\n"
    "format binary_little_endian 1.0\n"
    "comment Driftless sparse reconstruction\n"
    "element vertex {n}\n"
    "property float x\n"
    "property float y\n"
    "property float z\n"
    "property uchar red\n"
    "property uchar green\n"
    "property uchar blue\n"
    "end_header\n"
)

_VERTEX_DTYPE = np.dtype(
    [("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("red", "u1"), ("green", "u1"), ("blue", "u1")]
)


def write_ply(path: Path, xyz: list[float] | np.ndarray, rgb: list[int] | np.ndarray) -> int:
    """Write the full-resolution cloud as binary little-endian PLY. Returns vertex count."""
    pts = np.asarray(xyz, dtype=np.float32).reshape(-1, 3)
    cols = np.asarray(rgb, dtype=np.uint8).reshape(-1, 3)
    if len(cols) != len(pts):
        raise ValueError(f"rgb has {len(cols)} entries for {len(pts)} points")

    rows = np.empty(len(pts), dtype=_VERTEX_DTYPE)
    rows["x"], rows["y"], rows["z"] = pts[:, 0], pts[:, 1], pts[:, 2]
    rows["red"], rows["green"], rows["blue"] = cols[:, 0], cols[:, 1], cols[:, 2]

    with path.open("wb") as fh:
        fh.write(PLY_HEADER.format(n=len(pts)).encode("ascii"))
        fh.write(rows.tobytes())
    return len(pts)


def read_ply(path: Path) -> tuple[int, np.ndarray]:
    """Parse a PLY written by `write_ply` back. Used by the tests to assert byte sanity."""
    with path.open("rb") as fh:
        header = b""
        while b"end_header\n" not in header:
            chunk = fh.read(1)
            if not chunk:
                raise ValueError("truncated PLY header")
            header += chunk
        text = header.decode("ascii")
        if "format binary_little_endian 1.0" not in text:
            raise ValueError("not a binary little-endian PLY")
        count = next(
            int(line.split()[2])
            for line in text.splitlines()
            if line.startswith("element vertex")
        )
        rows = np.frombuffer(fh.read(count * _VERTEX_DTYPE.itemsize), dtype=_VERTEX_DTYPE)
    if len(rows) != count:
        raise ValueError(f"PLY declares {count} vertices but body holds {len(rows)}")
    return count, rows


def write_tum(path: Path, poses: list[dict[str, Any]]) -> int:
    """TUM trajectory: `timestamp tx ty tz qx qy qz qw`, one line per processed frame.

    The contract carries quaternions as [qw, qx, qy, qz]; TUM wants scalar-last, so reorder.
    """
    lines = []
    for pose in poses:
        tx, ty, tz = pose["position"]
        qw, qx, qy, qz = pose["quaternion"]
        lines.append(
            f"{pose['t_s']:.6f} {tx:.6f} {ty:.6f} {tz:.6f} "
            f"{qx:.6f} {qy:.6f} {qz:.6f} {qw:.6f}"
        )
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="ascii")
    return len(lines)


def read_tum(path: Path) -> list[list[float]]:
    """Parse a TUM file back into rows of 8 floats, skipping comments."""
    rows: list[list[float]] = []
    for line in path.read_text(encoding="ascii").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [float(v) for v in line.split()]
        if len(parts) != 8:
            raise ValueError(f"TUM line has {len(parts)} fields, expected 8: {line!r}")
        rows.append(parts)
    return rows


WALL_MS_DEFINITION = (
    "Span on CLOCK_MONOTONIC (shared by the API and worker processes) from upload-acceptance "
    "to reconstruction-ready, minus queue_wait_ms. Upload-acceptance is the instant the clip "
    "has been streamed to disk, proved decodable and issued a job id. Reconstruction-ready is "
    "the instant poses and map points exist, the full-resolution PLY and the TUM trajectory "
    "are written, and the web point cloud has been subsampled to SLAM_MAX_POINTS_WEB. "
    "INCLUDED: process-pool dispatch and argument pickling, video decode, feature tracking, "
    "loop-closure search, bundle adjustment and pose-graph optimisation, PLY and TUM "
    "serialisation, and the quality-aware point subsample. EXCLUDED: the client's upload "
    "time on the wire and the admission-time decode probe (both before the clock starts); "
    "any time queued behind another job (reported separately as queue_wait_ms, never folded "
    "in); and the terminal write of the reconstruction JSON plus its gzip, which cannot be "
    "inside the number because those bytes embed the metrics — it is measured instead as "
    "payload_emit_ms, and accept_to_bytes_on_disk_ms is the sum for anyone who wants it."
)


def build_report(
    job: dict[str, Any],
    config: dict[str, Any],
    recon_summary: dict[str, Any],
    timing: dict[str, int],
) -> dict[str, Any]:
    """`export/report.json` — everything needed to reproduce and audit the timing claim."""
    return {
        "job_id": job["job_id"],
        "filename": job["filename"],
        "created_at": job["created_at"],
        "status": job["status"],
        "truncated": job["truncated"],
        "video": job["video"],
        "config": config,
        "metrics": job["metrics"],
        "reconstruction": recon_summary,
        "timing": timing,
        "timing_definition": {
            "wall_ms": WALL_MS_DEFINITION,
            "queue_wait_ms": (
                "Span from upload-acceptance to the moment a worker slot was acquired. A few "
                "milliseconds of task-scheduling latency on an idle host; seconds when the "
                "single-job pool is already busy."
            ),
            "payload_emit_ms": (
                "Cost of writing reconstruction.json and the gzipped web payload after the "
                "wall_ms measurement point."
            ),
        },
    }


def subsample_for_web(points: dict[str, Any], cap: int) -> dict[str, list[Any]]:
    """Cap the web cloud at `cap` points, quality-first.

    Ranking is by observation count descending, ties broken by per-point reprojection error
    ascending (when the engine supplies one). A pure top-N would silently delete whole regions
    of the map that were only ever seen by a handful of keyframes — typically the end of the
    trajectory — so the last 15% of the budget is filled by a uniform stride over the
    remaining points to preserve spatial coverage. Deterministic: no RNG.
    """
    xyz = np.asarray(points["xyz"], dtype=np.float32).reshape(-1, 3)
    rgb = np.asarray(points["rgb"], dtype=np.uint8).reshape(-1, 3)
    obs = np.asarray(points["observations"], dtype=np.int32)
    n = len(xyz)

    if n <= cap:
        keep = np.arange(n)
    else:
        err = points.get("reprojection_error")
        err_arr = (
            np.asarray(err, dtype=np.float32)
            if err is not None and len(err) == n
            else np.zeros(n, dtype=np.float32)
        )
        # lexsort uses the LAST key as primary: observations desc, then error asc.
        order = np.lexsort((err_arr, -obs))
        quality_n = int(cap * 0.85)
        quality = order[:quality_n]
        rest = order[quality_n:]
        coverage_n = cap - quality_n
        stride = max(1, len(rest) // coverage_n)
        coverage = rest[:: stride][:coverage_n]
        keep = np.sort(np.concatenate([quality, coverage]))

    return {
        # 5 decimals is sub-millimetre in up-to-scale units and roughly halves the JSON.
        "xyz": np.round(xyz[keep], 5).ravel().tolist(),
        "rgb": rgb[keep].ravel().tolist(),
        "observations": obs[keep].tolist(),
    }


def web_reconstruction(full: dict[str, Any], cap: int) -> dict[str, Any]:
    """Project the on-disk reconstruction onto the exact contract shape, point-capped."""
    return {
        "job_id": full["job_id"],
        "poses": full["poses"],
        "points": subsample_for_web(full["points"], cap),
        "loop_closures": full["loop_closures"],
        "metrics": full["metrics"],
    }


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))


def probe_video(path: Path) -> dict[str, Any]:
    """Read video metadata with OpenCV — no ffprobe binary required in the image.

    Raises ValueError if the file does not decode, which is how the upload path proves a clip
    is real before a job id is ever issued.
    """
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError("file could not be opened as video")
        ok, frame = cap.read()
        if not ok or frame is None:
            raise ValueError("file opened but no frame could be decoded")
        height, width = frame.shape[:2]
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        cap.release()

    # Some containers report nonsense fps; 30 is the sane default for phone capture.
    if not (0.1 < fps < 1000.0):
        fps = 30.0
    if frame_count <= 0:
        frame_count = _count_frames(path)
    return {
        "width": int(width),
        "height": int(height),
        "fps": round(fps, 3),
        "frame_count": frame_count,
        "duration_s": round(frame_count / fps, 3) if fps else 0.0,
    }


def _count_frames(path: Path) -> int:
    """Fallback for containers with no frame index (some webm/mkv): decode and count."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    count = 0
    try:
        while cap.grab():
            count += 1
    finally:
        cap.release()
    return count


def render_preview(
    video_path: Path, frame_index: int, pose: dict[str, Any] | None, max_features: int
) -> bytes:
    """JPEG of one processed frame with tracked features drawn.

    The contracted `Reconstruction` carries neither per-frame keypoints nor camera intrinsics,
    so map-point reprojection is not reconstructible from it. We therefore re-detect ORB on
    the frame with the same feature budget the tracker used and draw those, annotated with the
    tracked-point count and reprojection error the engine actually reported for this frame.
    """
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, float(frame_index))
        ok, frame = cap.read()
        if not ok or frame is None:
            raise LookupError(f"frame {frame_index} is not decodable")
    finally:
        cap.release()

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(nfeatures=max_features)
    keypoints = orb.detect(gray, None)
    for kp in keypoints:
        x, y = int(kp.pt[0]), int(kp.pt[1])
        radius = max(2, int(kp.size / 4))
        cv2.circle(frame, (x, y), radius, (0, 255, 96), 1, lineType=cv2.LINE_AA)

    label = f"frame {frame_index}  orb {len(keypoints)}"
    if pose is not None:
        label += f"  tracked {pose['tracked_points']}  reproj {pose['reprojection_error_px']:.2f}px"
        if pose["is_keyframe"]:
            label += "  KF"
    # Scale the overlay to the frame so a 320px preview is as legible as a 1920px one.
    width = frame.shape[1]
    scale = max(0.32, min(0.7, width / 1100.0))
    bar = int(22 * max(scale, 0.4) / 0.5)
    cv2.rectangle(frame, (0, 0), (width, bar), (0, 0, 0), -1)
    cv2.putText(
        frame,
        label,
        (6, int(bar * 0.72)),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )

    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return bytes(buf.tobytes())
