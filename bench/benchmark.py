"""Benchmark the SLAM pipeline and write bench/results.json.

    python bench/benchmark.py                  # every clip in samples/, 3 repeats
    python bench/benchmark.py --repeats 5
    python bench/benchmark.py --video clip.mp4 --repeats 1
    python bench/benchmark.py --ablate         # also run loop-closure / BA off

Reports the per-stage timing split, throughput, ATE against ground truth where a
`<stem>_gt.json` exists, and reprojection error before and after bundle
adjustment. All numbers are measured, none are estimated.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slam import SlamConfig, SlamPipeline  # noqa: E402
from slam.geometry import align_trajectories  # noqa: E402
from slam.pipeline import _host_info  # noqa: E402

SAMPLES = ROOT / "samples"


@dataclass
class Run:
    wall_s: float
    decode_ms: float
    feature_ms: float
    tracking_ms: float
    optimize_ms: float
    processing_fps: float
    realtime_factor: float
    frames_processed: int
    pose_coverage: int
    keyframes: int
    map_points: int
    mean_reprojection_error_px: float
    ba_before_px: float
    ba_after_px: float
    ba_runs: int
    loop_closures: int
    loop_candidates: int
    lost_events: int
    focal_px: float
    drift: dict[str, float] = field(default_factory=dict)
    ate_m: float | None = None
    ate_scale: float | None = None


def ground_truth(video: Path) -> tuple[np.ndarray, dict] | None:
    meta_path = video.with_name(video.stem + "_gt.json")
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())
    return np.array([f["position"] for f in meta["frames"]], dtype=np.float64), meta


def one_run(video: Path, cfg: SlamConfig, gt: tuple[np.ndarray, dict] | None) -> Run:
    pipe = SlamPipeline(cfg)
    t0 = time.perf_counter()
    recon = pipe.run(video)
    wall = time.perf_counter() - t0
    m, t = recon.metrics, recon.timings

    ate = scale = None
    if gt is not None and len(recon.poses) > 5:
        idx = [p.frame_index for p in recon.poses]
        s, _, _, ate = align_trajectories(recon.positions(), gt[0][idx])
        scale = float(s)

    return Run(
        wall_s=round(wall, 3),
        decode_ms=t["decode_ms"],
        feature_ms=t["feature_ms"],
        tracking_ms=t["tracking_ms"],
        optimize_ms=t["optimize_ms"],
        processing_fps=t["processing_fps"],
        realtime_factor=t["realtime_factor"],
        frames_processed=m.frames_processed,
        pose_coverage=len(recon.poses),
        keyframes=m.keyframes,
        map_points=m.map_points,
        mean_reprojection_error_px=m.mean_reprojection_error_px,
        ba_before_px=t["ba_median_px"][0],
        ba_after_px=t["ba_median_px"][1],
        ba_runs=t["ba_runs"],
        loop_closures=m.loop_closures,
        loop_candidates=m.loop_candidates_checked,
        lost_events=t["lost_events"],
        focal_px=round(recon.intrinsics["fx"], 1),
        drift=m.drift.to_dict(),
        ate_m=None if ate is None else round(float(ate), 4),
        ate_scale=None if scale is None else round(scale, 4),
    )


def aggregate(runs: list[Run]) -> dict[str, Any]:
    def med(attr: str) -> float:
        vals = [getattr(r, attr) for r in runs if getattr(r, attr) is not None]
        return round(statistics.median(vals), 4) if vals else 0.0

    out = {k: med(k) for k in (
        "wall_s", "decode_ms", "feature_ms", "tracking_ms", "optimize_ms",
        "processing_fps", "realtime_factor", "mean_reprojection_error_px",
        "ba_before_px", "ba_after_px",
    )}
    out.update(
        frames_processed=runs[0].frames_processed,
        pose_coverage=runs[0].pose_coverage,
        keyframes=runs[0].keyframes,
        map_points=runs[0].map_points,
        loop_closures=runs[0].loop_closures,
        loop_candidates=runs[0].loop_candidates,
        lost_events=runs[0].lost_events,
        focal_px=runs[0].focal_px,
        drift=runs[0].drift,
        ate_m=runs[0].ate_m,
        ate_scale=runs[0].ate_scale,
        wall_s_spread=round(max(r.wall_s for r in runs) - min(r.wall_s for r in runs), 3),
    )
    return out


def table(rows: list[tuple[str, dict[str, Any]]]) -> str:
    head = (
        f"{'clip / variant':<34}{'wall':>7}{'fps':>7}{'xRT':>6}"
        f"{'dec':>7}{'feat':>7}{'trk':>8}{'opt':>8}{'kf':>5}{'pts':>7}"
        f"{'rep_px':>8}{'ATE_m':>8}{'lc':>4}"
    )
    lines = [head, "-" * len(head)]
    for name, a in rows:
        ate = "-" if a["ate_m"] is None else f"{a['ate_m']:.3f}"
        lines.append(
            f"{name:<34}{a['wall_s']:>6.2f}s{a['processing_fps']:>7.1f}"
            f"{a['realtime_factor']:>6.2f}{a['decode_ms']:>7.0f}{a['feature_ms']:>7.0f}"
            f"{a['tracking_ms']:>8.0f}{a['optimize_ms']:>8.0f}{a['keyframes']:>5}"
            f"{a['map_points']:>7}{a['mean_reprojection_error_px']:>8.3f}{ate:>8}"
            f"{a['loop_closures']:>4}"
        )
    return "\n".join(lines)


VARIANTS: dict[str, dict[str, Any]] = {
    "default": {},
    "no-loop-closure": {"enable_loop_closure": False},
    "no-local-BA": {"enable_local_ba": False},
    "width-480": {"target_width": 480},
    "features-800": {"max_features": 800},
    "stride-2": {"frame_stride": 2},
    # Local BA is 40-47% of wall time on the deployment host and scales with
    # keyframe count, which is why the two keyframe-heavy clips are the ones that
    # miss the 10 s budget there. Halving its frequency is the only lever that
    # buys time without reducing pose density or feature count.
    "ba-every-2": {"ba_every_n_keyframes": 2},
    "ba-nfev-12": {"ba_max_nfev": 12},
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, action="append")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--ablate", action="store_true", help="also run the knob variants")
    ap.add_argument("--out", type=Path, default=ROOT / "bench" / "results.json")
    args = ap.parse_args()

    videos = args.video or sorted(SAMPLES.glob("*.mp4"))
    if not videos:
        raise SystemExit("no videos found; pass --video")

    variants = VARIANTS if args.ablate else {
        "default": {}, "no-loop-closure": {"enable_loop_closure": False}
    }

    host = _host_info()
    print(f"host: {host['cpu']}  {host['vcpu']} vCPU  {host['ram_gb']} GB  "
          f"({platform.system()} {platform.machine()})")
    print(f"libs: numpy {np.__version__}  opencv {cv2.__version__}  scipy {scipy.__version__}  "
          f"python {platform.python_version()}  cv2 threads {cv2.getNumThreads()}")
    print(f"repeats: {args.repeats}\n")

    results: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": host,
        "platform": f"{platform.system()} {platform.machine()}",
        "python": platform.python_version(),
        # The library versions are part of the measurement. results.json was once
        # published from NumPy 2.4.6 / OpenCV 4.14.0 while the deployed image pins
        # 2.1.3 / 4.10.0, and the two disagree on ATE by up to 80% -- the numbers
        # described a configuration nobody was running.
        "libraries": {
            "numpy": np.__version__,
            "opencv": cv2.__version__,
            "scipy": scipy.__version__,
        },
        "cv2_threads": cv2.getNumThreads(),
        "repeats": args.repeats,
        "config_defaults": SlamConfig().to_dict(),
        "clips": {},
    }
    rows: list[tuple[str, dict[str, Any]]] = []

    for video in videos:
        gt = ground_truth(video)
        entry: dict[str, Any] = {
            "file": video.name,
            "ground_truth": bool(gt),
            "variants": {},
        }
        if gt:
            entry["trajectory_length_m"] = gt[1].get("trajectory_length_m")
            entry["closed_loop"] = gt[1].get("closed_loop")
        for name, overrides in variants.items():
            runs = [one_run(video, SlamConfig(**overrides), gt) for _ in range(args.repeats)]
            agg = aggregate(runs)
            agg["overrides"] = overrides
            agg["runs"] = [asdict(r) for r in runs]
            entry["variants"][name] = agg
            rows.append((f"{video.stem} / {name}", agg))
            print(table([rows[-1]]).splitlines()[-1])
        results["clips"][video.stem] = entry

    print()
    print(table(rows))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2))
    try:
        shown = args.out.relative_to(ROOT)
    except ValueError:
        shown = args.out
    print(f"\nwrote {shown}")


if __name__ == "__main__":
    main()
