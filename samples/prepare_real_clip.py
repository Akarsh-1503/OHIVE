#!/usr/bin/env python3
"""Cut the bundled real-world demo clip from the TUM RGB-D benchmark.

Source sequence: ``rgbd_dataset_freiburg1_desk`` — handheld colour camera sweeping over an
office desk, 640x480 @ 30 Hz, with 100 Hz motion-capture ground truth.

    TUM RGB-D SLAM Dataset and Benchmark, Computer Vision Group, TU Munich.
    J. Sturm, N. Engelhard, F. Endres, W. Burgard, D. Cremers,
    "A Benchmark for the Evaluation of RGB-D SLAM Systems", IROS 2012.
    https://cvg.cit.tum.de/data/datasets/rgbd-dataset
    Licensed CC BY 4.0 — https://creativecommons.org/licenses/by/4.0/

This script performs the only modifications we make: select 300 consecutive colour frames
(exactly 10.0 s at the sequence's native ~30 Hz), encode them to H.264, and resample the
mocap trajectory onto those frame timestamps. No cropping, scaling or colour grading.

    curl -O https://cvg.cit.tum.de/rgbd/dataset/freiburg1/rgbd_dataset_freiburg1_desk.tgz
    tar -xzf rgbd_dataset_freiburg1_desk.tgz
    python prepare_real_clip.py --source rgbd_dataset_freiburg1_desk
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent

N_FRAMES = 300
FPS = 30.0

# Two sequences from the same benchmark, deliberately chosen to bracket the difficulty
# range. fr1/desk is aggressive handheld motion (peaks at 7.6 deg/frame) and is the clip
# that stresses the tracker; fr3/long_office is the same kind of scene shot deliberately
# (peaks near 1 deg/frame) and is the like-for-like "ordinary handheld video" case.
# Reporting only one of them would misrepresent the system in one direction or the other.
SEQUENCES = {
    "desk_handheld_tum": {
        "sequence": "rgbd_dataset_freiburg1_desk",
        "group": "freiburg1",
        "start_frame": 100,     # this window revisits its own start (~5 cm)
        "anchor": "freiburg1_desk",
        # TUM's published intrinsics for the freiburg1 colour camera (raw stream).
        "intrinsics": {
            "model": "pinhole_radtan",
            "fx": 517.306408, "fy": 516.469215, "cx": 318.643040, "cy": 255.313989,
            "distortion": [0.262383, -0.953104, -0.005358, 0.002628, 1.163314],
        },
    },
    "office_handheld_tum": {
        "sequence": "rgbd_dataset_freiburg3_long_office_household",
        "group": "freiburg3",
        # Chosen by scanning every 10 s window in the mocap for the one that minimises
        # peak rotation while still covering ground and returning near its start.
        "start_frame": 2250,
        "anchor": "freiburg3_long_office_household",
        # freiburg3 colour camera. The published fr3 stream is already undistorted.
        "intrinsics": {
            "model": "pinhole",
            "fx": 535.4, "fy": 539.2, "cx": 320.1, "cy": 247.6,
            "distortion": [0.0, 0.0, 0.0, 0.0, 0.0],
        },
    },
}


def attribution(spec: dict, start_frame: int) -> dict:
    return {
        "dataset": "TUM RGB-D SLAM Dataset and Benchmark",
        "sequence": spec["sequence"],
        "authors": ("J. Sturm, N. Engelhard, F. Endres, W. Burgard, D. Cremers — "
                    "Computer Vision Group, Technical University of Munich"),
        "url": ("https://cvg.cit.tum.de/data/datasets/rgbd-dataset/download#"
                + spec["anchor"]),
        "archive": (f"https://cvg.cit.tum.de/rgbd/dataset/{spec['group']}/"
                    f"{spec['sequence']}.tgz"),
        "licence": "CC BY 4.0",
        "licence_url": "https://creativecommons.org/licenses/by/4.0/",
        "citation": ("J. Sturm, N. Engelhard, F. Endres, W. Burgard, D. Cremers, "
                     "'A Benchmark for the Evaluation of RGB-D SLAM Systems', "
                     "Proc. IROS, 2012."),
        "modifications": (f"Selected colour frames [{start_frame}, "
                          f"{start_frame + N_FRAMES}) from rgb.txt and encoded them to "
                          f"H.264 (crf 23, preset slow) at {FPS:g} fps. The mocap "
                          "trajectory was resampled onto those frame timestamps by "
                          "nearest-neighbour lookup. No cropping, rescaling or colour "
                          "adjustment."),
    }


def read_table(path: Path) -> list[list[str]]:
    return [ln.split() for ln in path.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, required=True,
                    help="extracted rgbd_dataset_* directory")
    ap.add_argument("--clip", choices=sorted(SEQUENCES), default="desk_handheld_tum",
                    help="which bundled clip to regenerate")
    ap.add_argument("--start-frame", type=int, default=None,
                    help="override the chosen window start")
    ap.add_argument("--out", type=Path, default=HERE)
    args = ap.parse_args()

    STEM = args.clip
    spec = SEQUENCES[STEM]
    START_FRAME = args.start_frame if args.start_frame is not None else spec["start_frame"]
    FR1_INTRINSICS = spec["intrinsics"]
    ATTRIBUTION = attribution(spec, START_FRAME)

    rgb = read_table(args.source / "rgb.txt")
    gt = np.array([[float(x) for x in row] for row in
                   read_table(args.source / "groundtruth.txt")])
    window = rgb[START_FRAME:START_FRAME + N_FRAMES]
    if len(window) != N_FRAMES:
        raise SystemExit(f"need {N_FRAMES} frames from index {START_FRAME}, got {len(window)}")
    stamps = np.array([float(t) for t, _ in window])

    # Nearest mocap sample per frame. The mocap runs at 100 Hz and covers the whole
    # colour stream, so this is always within a few milliseconds.
    idx = np.clip(np.searchsorted(gt[:, 0], stamps), 1, len(gt) - 1)
    idx = np.where(np.abs(gt[idx - 1, 0] - stamps) < np.abs(gt[idx, 0] - stamps), idx - 1, idx)
    poses = gt[idx]
    align_ms = float(np.abs(poses[:, 0] - stamps).max() * 1000.0)

    out_video = args.out / f"{STEM}.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        for i, (_, rel) in enumerate(window):
            shutil.copyfile(args.source / rel, Path(tmp) / f"f{i:04d}.png")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-framerate", f"{FPS:g}",
             "-i", str(Path(tmp) / "f%04d.png"),
             "-c:v", "libx264", "-preset", "slow", "-crf", "23",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_video)],
            check=True)

    t0 = stamps[0]
    xyz = poses[:, 1:4]
    length = float(np.linalg.norm(np.diff(xyz, axis=0), axis=1).sum())
    revisit = float(np.linalg.norm(xyz[:40, None, :] - xyz[None, -40:, :], axis=2).min())

    frames = []
    tum_lines = []
    for i, p in enumerate(poses):
        tx, ty, tz, qx, qy, qz, qw = p[1:8]
        frames.append({"frame_index": i, "t_s": round(i / FPS, 6),
                       "source_timestamp": round(float(stamps[i]), 6),
                       "position": [round(float(v), 6) for v in (tx, ty, tz)],
                       "quaternion": [round(float(v), 8) for v in (qw, qx, qy, qz)]})
        tum_lines.append(f"{(stamps[i] - t0):.6f} {tx:.6f} {ty:.6f} {tz:.6f} "
                         f"{qx:.8f} {qy:.8f} {qz:.8f} {qw:.8f}")

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", str(out_video)],
        check=True, capture_output=True, text=True).stdout.strip().split(",")
    width, height = int(probe[0]), int(probe[1])

    doc = {
        "video": out_video.name,
        "generator": "prepare_real_clip.py",
        "units": "metres",
        "width": width, "height": height, "fps": FPS, "frame_count": N_FRAMES,
        "intrinsics": FR1_INTRINSICS,
        "convention": (
            "Poses are TUM's published motion-capture trajectory, unmodified: T_wc in the "
            "mocap world frame (NOT re-expressed relative to frame 0). `position` is the "
            "colour-camera optical centre and `quaternion` is [qw, qx, qy, qz]. The TUM "
            "file order is (tx ty tz qx qy qz qw); it is reordered here to match the "
            "Driftless reconstruction contract."
        ),
        "closed_loop": True,
        "trajectory_length_m": round(length, 4),
        "start_end_revisit_m": round(revisit, 4),
        "gt_alignment_max_ms": round(align_ms, 3),
        "source": ATTRIBUTION,
        "frames": frames,
    }
    (args.out / f"{STEM}_gt.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    (args.out / f"{STEM}_gt_tum.txt").write_text(
        "# TUM RGB-D rgbd_dataset_freiburg1_desk mocap ground truth, CC BY 4.0\n"
        "# timestamp tx ty tz qx qy qz qw  (timestamps rebased to clip start)\n"
        + "\n".join(tum_lines) + "\n", encoding="utf-8")

    entry = {
        "id": "desk_handheld",
        "name": "Handheld office desk (TUM fr1/desk)",
        "description": ("Real handheld 640x480 capture sweeping an office desk and "
                        "returning to its starting viewpoint, with 100 Hz motion-capture "
                        "ground truth. TUM RGB-D benchmark, CC BY 4.0."),
        "duration_s": round(N_FRAMES / FPS, 3),
        # `url` is the public API route per the contract; `file` is the path relative to
        # this directory, which is how the backend locates the media on disk.
        "url": "/api/v1/samples/desk_handheld/file",
        "file": out_video.name,
    }
    manifest_path = args.out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    manifest = [m for m in manifest if m.get("id") != entry["id"]] + [entry]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"{out_video.name}: {width}x{height} {N_FRAMES} frames @ {FPS:g} fps, "
          f"{out_video.stat().st_size / 1e6:.2f} MB")
    print(f"  trajectory {length:.2f} m, start/end revisit {revisit:.3f} m, "
          f"gt alignment <= {align_ms:.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
