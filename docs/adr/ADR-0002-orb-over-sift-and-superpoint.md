# ADR-0002 — ORB over SIFT, AKAZE or SuperPoint

**Status:** accepted · **Date:** 2026-09 · **Follows:** [ADR-0001](ADR-0001-classical-sparse-slam-over-learned-methods.md)

## Context

Given a classical sparse pipeline, the feature detector and descriptor set the cost of the two
hottest operations in the system: detection (once per frame, 300 times) and matching (several
times per frame, against the previous frame, the local map, and candidate keyframes).

At 640 px with 1200 features per frame and a 10 s budget, feature extraction and matching get
roughly **7 ms per frame** before they start eating the optimisation budget.

## Options

### SIFT

Best-in-class classical repeatability, and scale/rotation invariant in a way ORB only
approximates. But: a **128-dimensional float** descriptor, matched with L2 distance. A
1200 × 1200 match is 1.44 M dot products in 128 dimensions. ORB's 32-byte binary descriptor
matches with **popcount of XOR** — on any modern CPU that is a handful of instructions per
pair, and OpenCV's `batchDistance` with `NORM_HAMMING` is SIMD-vectorised.

Measured in this system: a 1200 × 1200 ORB ratio-match is **0.65 ms**. SIFT at the same counts
is not in the same order of magnitude, and it is called several times per frame.

The licence objection has expired — SIFT's patent lapsed in 2020 and it moved into the main
OpenCV module — so this rejection is purely about speed.

### AKAZE

Nonlinear scale space, better repeatability than ORB, and a binary (M-LDB) descriptor so
matching stays cheap. The problem is **detection**: building a nonlinear diffusion scale space
per frame is several times the cost of a FAST pyramid, and detection happens on every one of
300 frames. It buys repeatability in the stage that is already cheap and pays for it in the
stage that is on the critical path.

### SuperPoint / SuperGlue (and DISK, LightGlue, …)

Genuinely better matching, especially across wide baselines and viewpoint change — which is
this system's weak spot ([07 §1](../07-limitations.md)). But it is a neural network, and
[ADR-0001](ADR-0001-classical-sparse-slam-over-learned-methods.md) already established that
there is no GPU. On CPU, SuperPoint inference per frame is tens of milliseconds at best, and
SuperGlue's attention over ~1000 keypoints is worse. It would consume the entire budget doing
the one thing that is currently nearly free.

Licence is a secondary but real issue: SuperPoint and SuperGlue are released under terms that
restrict commercial use.

### ORB · **chosen**

Oriented FAST keypoints with a rotated BRIEF descriptor. BSD-licensed, in OpenCV's core
module, 32 bytes per descriptor.

## Decision

**ORB**, at 1200 features per frame over an 8 × 6 grid.

## Consequences

ORB's known weaknesses are real and were mitigated rather than ignored — each mitigation below
exists because the naive version measurably failed:

| ORB weakness | mitigation | measured |
|---|---|---|
| Keypoints clump on high-contrast patches, making PnP ill-conditioned | Over-detect at 2×, keep the top ~25 responses per cell of an 8 × 6 grid, then top up or trim globally | Implemented as two OpenCV calls plus vectorised NumPy ranking, not `rows × cols` separate `detect` calls |
| Binary descriptors are less discriminative than SIFT | Lowe ratio at **0.8**, not the textbook 0.75; geometric masks (proximity, epipolar) do the heavy filtering so the ratio test can afford to be loose | 0.75 discards ~45 % of correct matches on repetitive indoor texture |
| Weak under large viewpoint change | Map-point descriptors are **refreshed from the newest observing keyframe** | Without it the tracked count decays ~30 %/frame after ~10 frames of viewpoint change |
| Fails on low texture | One retry per frame at `fastThreshold=5` (from 12) when a frame yields < 600 keypoints | Bounded: one extra `detect`, only on frames that need it |
| Not scale invariant in the SIFT sense | 8-level pyramid at `scaleFactor=1.2`; octave is carried per keypoint | — |

Measured ORB yield on the bundled clips, on the **decoded** frames the tracker actually sees:

| clip | median @ cap 1200 | median uncapped | 5th pct | min |
|---|---|---|---|---|
| `synthetic_loop` | 1200 | 5128 | 2603 | 2189 |
| `synthetic_corridor` | 1200 | 8140 | 6607 | 6218 |
| `desk_handheld_tum` | 1200 | 4195 | 1049 | 558 |

Both synthetic clips saturate the cap on every frame; the real clip saturates on the median
frame and never drops below 558 even during the fast pan. The 1200 cap is therefore a
*budget*, not a limit imposed by the scene — which is the right way round.

**The cost:** the 144/300 tracking gap on the real handheld clip
([07 §1](../07-limitations.md)) is partly a descriptor-matching failure across a fast pan, and
a learned matcher with a GPU would likely close it. That trade is accepted here, not denied.

`max_features` is exposed as `SLAM_MAX_FEATURES`; the `features-800` ablation measures what
lowering it costs (wall 4.32 → 3.52 s on the desk clip, ATE 0.204 → 0.114 m — which is faster
*and* more accurate on that particular clip, and worse on the corridor: 0.026 → 0.519 m).
