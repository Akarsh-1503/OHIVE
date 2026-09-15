# Driftless sample clips

Three 10-second demo clips, all with ground-truth camera trajectories, so a reviewer can
run the pipeline and check the drift numbers rather than take them on faith.

```
synthetic_loop.mp4            closed loop, full ground truth (trajectory + point cloud)
synthetic_corridor.mp4        open-ended forward translation, full ground truth
office_handheld_tum.mp4       real handheld footage, steady motion, mocap ground truth (CC BY 4.0)
desk_handheld_tum.mp4         real handheld footage, aggressive motion, mocap ground truth (CC BY 4.0)
manifest.json                 GET /api/v1/samples payload
generate_synthetic.py         renders both synthetic clips and their ground truth
prepare_real_clip.py          cuts the real clip from the TUM RGB-D archive
```

## The clips

| file | res | fps | frames | duration | size | ground truth |
|---|---|---|---|---|---|---|
| `synthetic_loop.mp4` | 1280x720 | 30 | 300 | 10.000 s | 2.83 MB | trajectory + point cloud (exact) |
| `synthetic_corridor.mp4` | 1280x720 | 30 | 300 | 10.000 s | 4.19 MB | trajectory + point cloud (exact) |
| `office_handheld_tum.mp4` | 640x480 | 30 | 300 | 10.000 s | 1.37 MB | 100 Hz motion capture |
| `desk_handheld_tum.mp4` | 640x480 | 30 | 300 | 10.000 s | 2.27 MB | 100 Hz motion capture |

All three are H.264 (`-crf 23 -preset slow`), `yuv420p`, `+faststart`.

ORB feature yield on the **decoded** frames (`cv2.ORB_create(n).detect`), which is what the
tracker actually sees:

| clip | median @ cap 1200 | median uncapped | 5th pct uncapped | min uncapped |
|---|---|---|---|---|
| `synthetic_loop` | 1200 | 5128 | 2603 | 2189 |
| `synthetic_corridor` | 1200 | 8140 | 6607 | 6218 |
| `desk_handheld_tum` | 1200 | 4195 | 1049 | 558 |

Both synthetic clips saturate the 1200-feature cap on every single frame; the real clip
saturates on the median frame and never drops below 558 even during fast rotation.

### `synthetic_loop.mp4`

A virtual pinhole camera on a closed elliptical path (semi-axes 1.6 m x 1.2 m) inside a
7 m x 7 m x 3 m textured room containing crates, desks, a freestanding partition and
wall-mounted displays. Exactly one revolution over 300 frames, with handheld wobble built
from integer harmonics of the loop phase so the trajectory **closes exactly**: frame 299
sits 0.024 m from frame 0 and looks in the same direction. Path length 8.85 m, mean speed
0.89 m/s.

This is the clip that makes the drift claim checkable. Frame 0 and frame 299 view the same
corner of the room, so a correct loop closure should collapse the end-of-trajectory error
to roughly the 0.024 m residual, and the ground-truth file says exactly what that residual
is.

The room is deliberately larger than the camera loop: every frame holds near clutter
(~1 m) and a far wall (~5 m). A narrow depth range would make triangulation
ill-conditioned and any reported reprojection error meaningless.

### `synthetic_corridor.mp4`

A 2.6 m x 3 m x 18 m corridor with sectioned wall textures, posters, displays and crates.
The camera translates 9.6 m straight down +Z at ~0.98 m/s with a slow yaw drift and a
walking bob, and never revisits a viewpoint.

This is the honest counterexample: loop closure cannot fire, so whatever scale and
orientation drift the tracker accumulates stays in the result. If the write-up only showed
the loop clip, the drift numbers would be flattering.

### `office_handheld_tum.mp4`

Real handheld footage from TUM RGB-D `freiburg3_long_office_household`, shot with deliberate,
steady motion. This is the **like-for-like "ordinary handheld video" case**, and it exists
because shipping only `desk_handheld_tum.mp4` would have misrepresented the system in the
opposite direction: fr1/desk is one of the most aggressive sequences in the benchmark.

| | |
|---|---|
| peak rotation | **0.96 deg/frame** (against 7.63 for fr1/desk) |
| trajectory | 2.20 m, returns to within **0.31 m** of its start |
| result | **297/300 frames tracked, zero tracking-lost events**, ATE 0.027 m |

The window was chosen by scanning every 10 s span of the mocap for the one minimising peak
rotation while still covering ground and revisiting its start.

### `desk_handheld_tum.mp4`

Real handheld footage: 300 consecutive colour frames from
`rgbd_dataset_freiburg1_desk`, a camera sweeping over a cluttered office desk. Trajectory
length 5.30 m, and the window was chosen because the camera returns to within **0.051 m**
of its starting position — so this clip has a genuine loop closure in real data, validated
against real motion capture.

**Resolution note.** This clip is 640x480, not 1280x720. The source is 640x480 and
upscaling it would fabricate detail, change the feature statistics and then be thrown away
anyway, because the backend downsamples to `SLAM_TARGET_WIDTH=640` before tracking. The
`Job.video` object in the contract carries `width`/`height`, so the app handles it
natively. No cropping, rescaling or colour grading was applied.

## Ground truth

Each clip has `<stem>_gt.json` and `<stem>_gt_tum.txt`; the synthetic clips also have
`<stem>_gt.ply`.

`<stem>_gt.json`:

```jsonc
{
  "video": "synthetic_loop.mp4",
  "units": "metres",
  "width": 1280, "height": 720, "fps": 30.0, "frame_count": 300,
  "intrinsics": {"model": "pinhole", "fx": 900.0, "fy": 900.0,
                 "cx": 640.0, "cy": 360.0, "distortion": [0,0,0,0,0]},
  "convention": "...",
  "closed_loop": true,
  "trajectory_length_m": 8.8538,
  "loop_closure_gap_m": 0.024354,
  "frames": [
    {"frame_index": 0, "t_s": 0.0,
     "position": [1.575779, -0.000264, 0.011499],
     "quaternion": [0.94221539, 0.01012701, -0.33485273, -0.00112322]}
  ]
}
```

Pose convention, matching the `Reconstruction` object in `_contracts/assignment2-api.md`:
OpenCV camera axes (+Z forward, +X right, +Y down); each entry is **`T_wc`**
(world-from-camera), where `position` is the camera centre in world coordinates and
`quaternion` is `[qw, qx, qy, qz]` of the world-from-camera rotation, i.e.
`p_world = R * p_camera + position`.

For the synthetic clips the world frame is the fixed scene frame, which coincides with the
frame-0 camera only up to the frame-0 pose given in the file — align before comparing, or
use the TUM export with a standard tool.

Intrinsics used:

| clip | model | fx | fy | cx | cy | distortion |
|---|---|---|---|---|---|---|
| `synthetic_loop`, `synthetic_corridor` | pinhole | 900.0 | 900.0 | 640.0 | 360.0 | none (exact zero) |
| `office_handheld_tum` | pinhole | 535.4 | 539.2 | 320.1 | 247.6 | none (fr3 stream is undistorted) |
| `desk_handheld_tum` | pinhole + radtan | 517.306408 | 516.469215 | 318.643040 | 255.313989 | `[0.262383, -0.953104, -0.005358, 0.002628, 1.163314]` |

The synthetic renderer is a true pinhole with no distortion, so `fx = fy = 900` at
1280x720 is exact, not a calibration estimate — that is a 70.9 deg horizontal field of
view. The real clip carries TUM's published `freiburg1` intrinsics for the raw colour
stream.

`<stem>_gt_tum.txt` is the same trajectory in TUM format
(`timestamp tx ty tz qx qy qz qw`, timestamps rebased to the clip start), so
`evo_ape tum <stem>_gt_tum.txt estimated.txt -va` works directly against the
`GET /api/v1/jobs/{id}/export/tum` output.

`<stem>_gt.ply` is a binary-little-endian point cloud (~55 000 points, `float x y z` +
`uchar rgb`) sampled from the scene surfaces with colours taken from the same textures the
renderer used, area-weighted across planes. It is the exact cloud the reconstruction is
trying to recover — useful for a cloud-to-cloud error after Sim(3) alignment.

## Regenerating

```bash
pip install numpy opencv-python-headless          # plus ffmpeg on PATH

# Synthetic clips, ground truth and manifest entries (about 60 s on a laptop):
python generate_synthetic.py
python generate_synthetic.py --scene loop --seconds 2   # quick smoke render

# Real clip: fetch the source archive (344 MB), then cut it.
curl -O https://cvg.cit.tum.de/rgbd/dataset/freiburg1/rgbd_dataset_freiburg1_desk.tgz
tar -xzf rgbd_dataset_freiburg1_desk.tgz
python prepare_real_clip.py --source rgbd_dataset_freiburg1_desk
```

Both scripts are deterministic (fixed seeds, fixed frame window) and merge their own
entries into `manifest.json` rather than overwriting it.

Under the hood, the synthetic renderer is an exact inverse texture mapping: for every pixel
inside a plane's projected silhouette it back-projects the ray, intersects it with the
plane, converts the hit to texture coordinates and samples, keeping a z-buffer for
occlusion. That is slower than warping four corners with a homography, but it clips
correctly against the near plane and produces stable, well-localised corners — which is the
point, since ORB has to find them. Textures are procedural (plaster, planks, ceiling
panels, posters, displays) with scattered sub-pixel-scale speckle so corner density stays
high at every viewing distance.

Encoding is a raw BGR pipe into:

```
ffmpeg -f rawvideo -pix_fmt bgr24 -s 1280x720 -r 30 -i - \
       -c:v libx264 -preset slow -crf 23 -pix_fmt yuv420p -movflags +faststart out.mp4
```

## `manifest.json`

Matches the `GET /api/v1/samples` contract shape:
`[{"id", "name", "description", "duration_s", "url"}]`, plus one extra key.

`url` is the public API route `/api/v1/samples/{id}/file`, which the contract names as the
default target of `Sample.url`, so the backend can pass the manifest entry straight
through. `file` is the media path **relative to this directory** — that is how the backend
locates the bytes on disk, and it keeps the filename unambiguous instead of having the
server reverse-engineer it from the id. The backend drops `file` before serialising the
`Sample`.

## Provenance and licensing

**`synthetic_loop.mp4`, `synthetic_corridor.mp4` and their ground truth** were generated
entirely by `generate_synthetic.py` in this repository. No third-party assets, textures or
models are involved — every texture is procedural numpy. They are covered by this
repository's licence.

**`office_handheld_tum.*`, `office_handheld_tum_gt.json` and `office_handheld_tum_gt_tum.txt`**
are derived from the TUM RGB-D benchmark and are redistributed here under **CC BY 4.0**.

- Dataset: TUM RGB-D SLAM Dataset and Benchmark, Computer Vision Group, Technical
  University of Munich.
- Sequence: `rgbd_dataset_freiburg3_long_office_household`.
- Authors: J. Sturm, N. Engelhard, F. Endres, W. Burgard, D. Cremers.
- Landing page: <https://cvg.cit.tum.de/data/datasets/rgbd-dataset/download#freiburg3_long_office_household>
- Source archive: <https://cvg.cit.tum.de/rgbd/dataset/freiburg3/rgbd_dataset_freiburg3_long_office_household.tgz>
- Licence: CC BY 4.0 — <https://creativecommons.org/licenses/by/4.0/>
- Citation: J. Sturm, N. Engelhard, F. Endres, W. Burgard, D. Cremers, *A Benchmark for the
  Evaluation of RGB-D SLAM Systems*, Proc. IROS, 2012.
- Changes made: selected colour frames `[2250, 2550)` from `rgb.txt` and encoded them to
  H.264 at 30 fps; resampled the 100 Hz mocap trajectory onto those frame timestamps by
  nearest-neighbour lookup (worst-case alignment error 8.5 ms). No cropping, rescaling or
  colour adjustment.

**`desk_handheld_tum.mp4`, `desk_handheld_tum_gt.json` and `desk_handheld_tum_gt_tum.txt`**
are derived from the TUM RGB-D benchmark and are redistributed here under
**CC BY 4.0**.

- Dataset: TUM RGB-D SLAM Dataset and Benchmark, Computer Vision Group, Technical
  University of Munich.
- Sequence: `rgbd_dataset_freiburg1_desk`.
- Authors: J. Sturm, N. Engelhard, F. Endres, W. Burgard, D. Cremers.
- Landing page: <https://cvg.cit.tum.de/data/datasets/rgbd-dataset/download#freiburg1_desk>
- Source archive: <https://cvg.cit.tum.de/rgbd/dataset/freiburg1/rgbd_dataset_freiburg1_desk.tgz>
- Licence: CC BY 4.0 — <https://creativecommons.org/licenses/by/4.0/>
- Citation: J. Sturm, N. Engelhard, F. Endres, W. Burgard, D. Cremers, *A Benchmark for the
  Evaluation of RGB-D SLAM Systems*, Proc. IROS, 2012.
- Changes made: selected colour frames `[100, 400)` from `rgb.txt` and encoded them to
  H.264 at 30 fps; resampled the 100 Hz mocap trajectory onto those frame timestamps by
  nearest-neighbour lookup (worst-case alignment error 4.2 ms). No cropping, rescaling or
  colour adjustment.

The same attribution block is embedded in `desk_handheld_tum_gt.json` under `source`, so it
travels with the data.

Beyond these three clips the app accepts user uploads (`POST /api/v1/jobs`), so a reviewer
can drop in their own footage without any of the bundled samples.
