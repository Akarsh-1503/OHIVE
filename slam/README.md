# Driftless SLAM core

Monocular RGB sparse point-cloud SLAM. Pure Python on top of OpenCV, NumPy and
SciPy — no native extension to build, no GPU, no ROS.

```bash
pip install ./slam          # installs the `slam` package and its three deps
```

```python
from slam import SlamPipeline, SlamConfig

cfg = SlamConfig(target_width=640, max_features=1200,
                 enable_loop_closure=True, max_frames=1800)
recon = SlamPipeline(cfg).run("clip.mp4", on_progress=lambda ev: print(ev["event"]))

recon.to_dict()             # Reconstruction JSON, points capped at cfg.max_points_web
recon.to_ply("cloud.ply")   # binary PLY, full cloud
recon.to_tum("traj.txt")    # TUM trajectory, `tx ty tz qx qy qz qw`
recon.metrics               # Metrics dataclass
recon.timings               # measured stage split (the service owns wall_ms)
recon.intrinsics            # what tracking actually used, after self-calibration
```

## Pipeline

| stage | file | what it does |
|---|---|---|
| ingest | `pipeline.py` | `VideoCapture` + resize to `target_width`, on its own thread |
| features | `features.py` | grid-bucketed ORB, ~1200 keypoints spread over an 8x6 grid |
| calibration | `tracking.py` | focal from the two-view fundamental matrix, then refined by BA |
| initialisation | `tracking.py` | homography vs essential by score, cheirality + parallax gates |
| tracking | `tracking.py` | constant-velocity prediction, guided match, PnP RANSAC + LM |
| mapping | `mapping.py` | keyframes, epipolar-guided triangulation, point culling |
| local BA | `mapping.py` | sparse LM over a 7-keyframe window, analytic Jacobian, soft-L1 |
| loop closure | `loop.py` | tf-idf BoW + Sim(3) RANSAC + Sim(3) pose-graph Gauss-Newton |
| export | `io.py` | binary PLY, TUM trajectory |

## Robustness vs latency

The default configuration is sized for the 10 s-clip budget on an 8 vCPU host.
One knob trades latency for robustness on fast handheld footage:

```python
SlamConfig(kf_min_parallax_ratio=0.052)   # denser keyframes
```

Measured on `samples/desk_handheld_tum.mp4` (Apple M4, median of 2):

| setting | tracked frames | wall | ATE |
|---|---|---|---|
| default (`0.13`) | 144 / 300 | 4.3 s | 0.204 m |
| robust (`0.052`) | 286 / 300 | 10.1 s | 0.282 m |

The robust setting roughly doubles the keyframe count, which is what keeps
tracking alive through a fast pan, and it does not fit the latency budget. It
also makes loop-closure place recognition less selective — it produced two false
closures on the corridor clip, which by construction has no revisit. Use it only
when throughput does not matter.

## Scale

A single moving camera cannot observe absolute distance. The initial two-view
baseline is fixed to **1.0** and every length in the output — point coordinates,
`trajectory_length_m`, the drift numbers — is in those units. Compare
trajectories only after a Sim(3) alignment.

## Coordinates

OpenCV camera axes: +Z forward, +X right, +Y down. Poses are stored internally as
`T_cw` (camera-from-world) and reported as `T_wc` (world-from-camera): `position`
is the camera centre, `quaternion` is `[qw, qx, qy, qz]`. The TUM export reorders
to the scalar-last convention that format requires.

## Vocabulary

`vocab.npz` holds 1024 binary visual words plus tf-idf weights, built offline by
`bench/build_vocab.py` from the demo clips. If the file is missing the library
falls back to a deterministic random (LSH-style) vocabulary, which still works
but recognises places less reliably.
