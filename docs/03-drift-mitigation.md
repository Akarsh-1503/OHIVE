# 03 — Drift mitigation

> **This document answers the brief's requirement #4: "an approach to minimise accumulated
> pose error and drift."**

Every number here comes from [`../bench/results.json`](../bench/results.json), produced by
`make bench` (`bench/benchmark.py --repeats 5 --ablate`) on an Apple M4. Methodology and the
deployment-host figures are in [06 — Performance](06-performance.md).

---

## What drift actually is in a monocular system

Three distinct errors accumulate, and they need different answers:

| | what it is | what fixes it |
|---|---|---|
| **Rotational drift** | small yaw/pitch/roll errors compounding along the chain | robust cost functions, windowed BA, more parallax |
| **Translational drift** | position error growing roughly with path length | the same, plus loop closure |
| **Scale drift** | the *unit of length* changing along the trajectory | **only** an external constraint. Not BA — see [§7](#7-the-negative-results) |

The third is the one that makes monocular SLAM different from stereo or RGB-D, and it is why
this system's closure constraint is a `Sim(3)` similarity rather than an `SE(3)` rigid
transform. A `Sim(3)` edge carries a scale ratio; an `SE(3)` edge cannot represent the error
at all.

---

## The headline results

Absolute Trajectory Error after Sim(3) alignment to ground truth, shipped defaults, median of
5 runs:

| clip | trajectory | ATE, LC **on** | ATE, LC **off** | closures | candidates checked |
|---|---|---|---|---|---|
| `synthetic_loop` | 8.85 m | **0.711 m** | 0.718 m | 1 | 43 |
| `synthetic_corridor` | 9.72 m | **0.026 m** | 0.026 m | 0 — *correctly* 0, no revisit exists | 4 |
| `desk_handheld_tum` | 5.30 m | **0.204 m** | 0.168 m | 1 | 1 |

On the accepted closure of `synthetic_loop`:

| | |
|---|---|
| Loop-consistency error, before PGO | **2.80** (up-to-scale units) |
| After PGO, expressed in pre-optimisation units | **0.228** |
| **Reduction** | **91.9 %** |
| Sim(3) inliers | **167** |
| Scale correction applied | 0.9986 — i.e. scale had barely drifted, by design |
| Edge | keyframe 37 → keyframe 9 |

Reproduce:

```bash
make bench-quick                       # 1 repeat, all three clips, defaults
python bench/benchmark.py --repeats 5 --ablate    # what results.json contains
```

Three things in that table are worth saying out loud before anything else.

**The corridor's 0 closures is a result, not a gap.** `synthetic_corridor` translates 9.6 m
straight down a corridor and never revisits a viewpoint. Four candidates were proposed by
place recognition and all four were rejected by geometric verification. A system that
"closed a loop" there would be broken; this one correctly does not, and that is the whole
reason the clip ships.

**On `desk_handheld_tum`, loop closure makes ATE slightly *worse*** — 0.204 m with it, 0.168 m
without — even though the loop-consistency residual on that closure collapses 95 % (4.632 →
0.228). This is stated here rather than in a footnote because it is the sort of thing a
careful reviewer will find. The cause and the reasoning are in [§6](#6-where-it-still-loses).

**The loop clip's 0.711 m is not a good ATE**, and loop closure barely moves it (0.718 →
0.711). The closure is doing its job — the loop-consistency error collapses by 91.9 % — but
ATE is dominated by a different error that closure cannot see. Also [§6](#6-where-it-still-loses).

---

## 1. Parallax gating — the mechanism that matters most

Two-view triangulation has a **depth bias**, not just depth noise. Because depth is
proportional to `1/disparity` and disparity is the noisy quantity, Jensen's inequality
guarantees the expectation of the reconstructed depth exceeds the true depth. The bias scales
roughly as `1/parallax²`.

Measured on a synthetic bench with known geometry and 0.5 px keypoint noise:

| parallax | depth bias |
|---|---|
| 0.6° | **+12.6 %** |
| 1.4° | +1.3 % |
| 5.7° | +0.01 % |

An over-long depth inflates the next PnP pose, which inflates the next triangulation, which
inflates the next pose. That is the scale-drift feedback loop, and it starts at triangulation.

Four gates, at four stages:

| gate | value | where |
|---|---|---|
| Initialisation parallax | median > 2.0° | `tracking.py::Initializer.try_init` |
| Per-point init parallax | > 2.0° | `tracking.py::_filter_triangulation` |
| New-point triangulation | > 2.5° | `pipeline.py::_triangulate_new` |
| Keyframe insertion | `baseline/depth > 0.13` (≈ 7.4°) | `pipeline.py::_needs_keyframe` |

Plus a **depth cap**: a new point farther than `3 × ref_depth` is rejected outright, because
a point much farther away than the map it is joining is almost always a low-parallax
artefact.

**Measured**: the depth cap plus baseline-ordered neighbour selection took end-of-loop scale
inflation on `synthetic_loop` from **7.4× to ~1.1×**. That is the single largest drift win in
the system, and it happens before any optimiser runs.

---

## 2. Longest-baseline-first triangulation

Covisibility ranks the *most recent* keyframes highest. Those are exactly the
short-baseline, low-parallax, ill-conditioned pairs. Following covisibility order naively
means triangulating new structure from the worst available geometry.

So the neighbour pool is the union of the 20 most covisible keyframes and the last 12 by
index, **sorted by descending baseline**, filtered to `baseline/ref_depth > tan(2.5°)`, and
the first 12 are used. Long baselines get first pick of the unmatched keypoints; shorter ones
are consulted only if the keyframe still has fewer than 150 new points.

Both halves of that are load-bearing:

- Taking **only** long baselines starves the map — pose coverage fell to **143/300**.
- Taking **all** of them lets the ill-conditioned pairs set the scale.

This was one of the three changes that fixed the loop-closure regression in [§5](#5-loop-closure--and-the-regression-that-made-it-work).

---

## 3. Robust costs everywhere

One mismatched descriptor produces a ~50 px reprojection residual. Under plain least squares
that single term outweighs five hundred good ones.

| stage | robust treatment |
|---|---|
| Two-view init | RANSAC on `F` (1.0 px), `H` (2.5 px), `E` (1.0 px); χ²-gated symmetric scoring |
| Tracking | `solvePnPRansac`, 3 px, 0.995 confidence, then LM on the consensus set only |
| New points | Sampson distance < 3 px, then reprojection < 4 px in both views |
| Local BA | `soft_l1` with `f_scale = max(2.0, 1.4826·median(r₀))` — MAD-scaled Huber |
| Local BA, 2nd pass | observations still > 4 px after convergence are **deleted**, not just down-weighted |
| Loop verification | Sim(3) RANSAC, threshold = 10 % of destination-frame median depth |
| Pose graph | per-edge Huber, `w = 1/‖r‖` beyond `‖r‖ > 1` |

The MAD scaling detail is worth a sentence: a fixed 2 px kernel against 15 px residuals
flattens the cost surface and the trust-region solver stalls. Scaling the kernel to the
current residual distribution lets BA recover from a bad window, then collapses back to 2 px
once tracking is healthy.

---

## 4. Windowed local bundle adjustment

Seven newest keyframes free, every map point they observe free, every *other* keyframe that
observes those points **fixed as an anchor**. Runs after every keyframe insertion.

The fixed anchors are what stop the window from drifting away from the older map — the cheap
stand-in for proper marginalisation. And there must be **at least two** of them: with a single
fixed camera, rescaling every point and every free camera translation about it leaves all
projections identical. That exact null direction let the window shrink ~10 % per BA run early
in a sequence, which reads downstream as violent scale drift.

Measured, from the `no-local-BA` ablation:

| clip | ATE with BA | ATE without | reprojection with | without |
|---|---|---|---|---|
| `synthetic_corridor` | **0.026 m** | 0.061 m | 0.53 px | 0.72 px |
| `desk_handheld_tum` | **0.204 m** | 0.277 m | 1.42 px | 1.77 px |
| `synthetic_loop` | 0.711 m | 0.508 m | 1.17 px | 1.86 px |

The loop clip's row is the interesting one and it is not a typo. Without local BA that clip
accumulates an enormous scale error — `scale_drift_ratio` **8.48**, loop-consistency residual
**58.5** before PGO — and the loop closure then rescales the entire map to fix it, landing at
a lower ATE almost by accident after Sim(3) alignment to ground truth. The reconstruction it
produces is visibly wrong (reprojection error 1.86 px against 1.17 px); ATE-after-alignment
simply does not penalise a uniformly-wrong scale, because the alignment absorbs it. Which is
the same fact as [§7](#7-the-negative-results), seen from the metric side.

**Cost**: local BA is 25–52 % of the wall clock. Turning it off takes `synthetic_loop` from
4.76 s to 3.82 s. It is kept.

---

## 5. Loop closure — and the regression that made it work

### The four stages

1. **tf-idf bag of visual words** over a shipped 1024-word vocabulary with an inverted index.
2. **Candidate filtering**: ≥ 20 keyframes of temporal gap; a *relative* score bar
   (`0.72 ×` the weakest already-covisible neighbour's score) rather than an absolute one;
   covisibility-consistency across 2 consecutive keyframes; a 15-keyframe cooldown.
3. **Sim(3) RANSAC verification** against the candidate's local map, plus a guided-support
   re-match.
4. **Sim(3) pose-graph Gauss-Newton** on the Lie algebra with sparse normal equations,
   acceptance guards and rollback.

Mathematics in [02 — The SLAM algorithm](02-slam-algorithm.md) §7–9.

### The regression

An earlier iteration of this pipeline had loop closure making ATE **worse**:

| | ATE |
|---|---|
| `synthetic_loop`, loop closure **off** | 0.781 m |
| `synthetic_loop`, loop closure **on** | **1.140 m** ← worse |

A loop closure that increases trajectory error is worse than no loop closure, because it also
destroys the map. Three changes fixed it, and each one is a distinct failure mode.

**Fix 1 — longest-baseline-first triangulation.** The map being closed *onto* was itself
scale-drifted, because new structure was being triangulated from whatever covisibility ranked
highest, which is the shortest baselines. The closure was then asked to reconcile two
differently-scaled halves of a bad map. Fixing triangulation took end-of-loop scale inflation
from 7.4× to ~1.1× and made the closure's job tractable. ([§2](#2-longest-baseline-first-triangulation))

**Fix 2 — verify against the candidate's local map, not the single candidate keyframe.** One
keyframe owns a thin slice of structure. The true closure was failing Sim(3) RANSAC on **25**
correspondences, while the candidate's 8-keyframe neighbourhood offered roughly four times as
many. It now passes with **167 inliers**. Before this fix the system was accepting *marginal*
closures — the ones that scraped past the inlier floor — and rejecting the good one.

**Fix 3 — two acceptance guards, with rollback.** PGO runs on a copy of the vertex set, and
the result is only committed if both hold:

```
guard 1 (pre-check):   loop residual  ≤  1.2 × total keyframe path length
guard 2 (post-check):  1 − post/pre   ≥  0.75
```

Guard 1 rejects a closure demanding a correction comparable to the whole trajectory — that is
not a loop, it is two places that look alike. Guard 2 exists because **a Sim(3) pose graph
can satisfy almost any single loop edge by rescaling**, so the residual dropping is not by
itself evidence. A *true* closure's residual collapses; a false one fights the odometry chain
and settles at a compromise. Measured: **96–100 % reduction for real closures against 32 %
for the corridor false positive.**

The post-error is converted back into pre-optimisation units (`post × span_pre / span_post`)
before the ratio is taken, or the headline reduction would be partly just a change of ruler.

Alongside those, `_guided_support` re-projects the candidate's **entire** local map through
the estimated Sim(3) and re-matches within 12 px, requiring ≥ 45 recovered points. That is
the check that stopped a false closure on `synthetic_corridor`.

### After the fixes

| | ATE | loop residual |
|---|---|---|
| `synthetic_loop`, LC off | 0.718 m | — |
| `synthetic_loop`, LC on | **0.711 m** | 2.80 → 0.228 (**−91.9 %**), 167 inliers |
| `synthetic_corridor`, LC on | 0.026 m | no closure accepted, 4 candidates rejected |

The honest reading: loop closure is now **correct** — it fires on a true revisit, it collapses
the loop-consistency error by 92 %, and it refuses to fire when there is no revisit. Its
effect on *ATE* is small on this clip (−1 %) because ATE is dominated by something else
([§6](#6-where-it-still-loses)). The system went from "loop closure actively harmful" to
"loop closure correct and mildly positive", and the mechanism that produced that change is
the interesting part.

---

## 6. Where it still loses

### The loop clip's 0.711 m

Two causes, in order of size.

**Residual focal error.** Ground-truth focal at the 640 px tracking width is 450 px. Recovered:
**501.8 px**, +11.5 %. A wrong focal does not produce a random trajectory — it produces a
*systematically distorted* one, because depth and focal trade off directly in the projection.
Sim(3) alignment cannot absorb a shape distortion, only a similarity, so it lands entirely in
ATE. The corridor clip recovers 446.0 px (−0.9 %) and posts 0.026 m; that contrast is the
evidence.

**Depth inflation of 4–5× from low-parallax triangulation.** Even with every gate in
[§1](#1-parallax-gating--the-mechanism-that-matters-most), points triangulated near the
parallax floor sit too far away. This is unfixed and is named in
[07 — Limitations](07-limitations.md).

### Loop closure making `desk_handheld_tum` slightly worse

0.204 m with closure, 0.168 m without. The closure itself is sound — 4.632 → 0.228, a 95 %
collapse. What it costs is this: on the default settings that clip only tracks **144 of 300
frames** (a 151-frame blackout during a 7.6°/frame pan — see
[07 — Limitations](07-limitations.md)), so the trajectory is two disconnected arcs. The
closure re-anchors the graph and applies a global scale correction of 0.915 across both, and
the arc that did *not* need the correction absorbs some of it. With 145 tracked frames and no
closure, the map is also 84 % denser (4064 points against 2211), because the closure's
structure-only reconciliation pass culls observations it cannot explain across the seam.

It would be easy to hide this by turning loop closure off for that clip, or by reporting only
the synthetic results. Neither is honest. The correct summary is: **loop closure is a clear
win when tracking is continuous, and a small net negative when the trajectory is already
fragmented.** A closure between two disconnected arcs is doing structurally less than it
looks like it is doing.

### Scale drift without a revisit

`synthetic_corridor` never revisits, so nothing external ever constrains its scale. Its ATE is
excellent (0.026 m) **after Sim(3) alignment**, but its recovered scale factor is 0.55 —
i.e. the reconstruction is roughly half the size of reality, uniformly. That is invisible to
ATE-after-alignment by construction, and it is the right way to report a monocular system, but
it should not be mistaken for "no scale error".

---

## 7. The negative results

These are the most credible part of this document, because they are the experiments that
*failed*.

### Hartley–Sturm optimal triangulation: no measurable improvement

The obvious response to depth bias is a better triangulator. The DLT method minimises an
algebraic error; Hartley & Sturm's optimal method minimises the true geometric reprojection
error by solving a degree-6 polynomial for the epipolar-consistent correction. It is
textbook-correct and strictly better in the least-squares sense.

Measured depth bias on the same synthetic bench, 0.5 px noise, 0.6° parallax:

| method | depth bias |
|---|---|
| DLT (`cv2.triangulatePoints`) | **+12.66 %** |
| Hartley–Sturm optimal | **+12.57 %** |

Identical to within noise. **It was not adopted.**

The reason is the useful part. The bias is **not** caused by the triangulator's choice of
error metric. It is caused by the nonlinearity of inverse depth under noise: depth `∝
1/disparity`, disparity is the noisy quantity, and `E[1/x] > 1/E[x]` by Jensen. *Every*
two-view estimator inherits it, because it is a property of the parameterisation and the noise
model, not of the objective function.

The only thing that removes it is **more parallax** — which is why the engineering effort went
into [§1](#1-parallax-gating--the-mechanism-that-matters-most) and
[§2](#2-longest-baseline-first-triangulation) instead, and why those produced a 7.4× → 1.1×
improvement where a better triangulator produced 0.09 percentage points.

### Inverse-depth BA parameterisation: no improvement, and the reason is the sharpest thing here

The standard remedy for ill-conditioned depth in bundle adjustment is to parameterise points
by inverse depth in an anchor frame rather than by XYZ, because inverse depth is far closer to
Gaussian under pixel noise and remains well-behaved for distant points. It was implemented and
measured. It changed nothing.

**Because scale drift is invisible to bundle adjustment.**

Take any monocular reconstruction — poses `{T_i}` and points `{X_j}` — and scale it:

```
X_j → s·X_j ,     t_i → s·t_i ,     R_i unchanged
```

Then for every observation:

```
π(K, T'_i, X'_j) = ( f·(s·R_i X_j + s·t_i)_x / (s·R_i X_j + s·t_i)_z + c_x , … )
                 = ( f·(R_i X_j + t_i)_x / (R_i X_j + t_i)_z + c_x , … )
                 = π(K, T_i, X_j)
```

The `s` cancels identically in the perspective division. **Every reprojection residual is
bit-identical.** A uniformly rescaled map and trajectory sit at exactly the same point on the
cost surface — it is a one-dimensional null direction of the BA Hessian, not a shallow
valley.

Consequences, all of which the design reflects:

1. No reparameterisation of the points can help, because the problem is not conditioning. The
   direction has **zero** curvature, not small curvature.
2. Global BA cannot remove scale drift either. Only *local* scale inconsistency — a region
   whose scale disagrees with its neighbours' — is visible, and only through the shared
   observations that link them.
3. The only thing that can observe accumulated scale error is an **external constraint**, and
   in a pure monocular system the only one available is a revisited place. Hence: `Sim(3)`
   loop closure, not `SE(3)`; the scale row of the pose-graph information matrix left at
   identity so the correction redistributes around the loop; and `scale_drift_ratio` reported
   as a first-class metric.
4. It is also why the post-closure reconciliation is **structure-only** BA with poses held
   fixed. PGO has just used information BA cannot see. Re-opening the poses lets BA trade that
   information away for a marginally lower reprojection error — a strictly bad trade that
   looks like an improvement on the metric BA optimises.

This one fact reorganised the whole drift strategy. It is the reason the answer to "how do you
stop monocular drift" is not "run a better optimiser".

---

## 8. Summary — every mechanism, and what it is worth

| # | mechanism | measured effect |
|---|---|---|
| 1 | Parallax gates at four stages + depth cap | end-of-loop scale inflation **7.4× → ~1.1×** |
| 2 | Longest-baseline-first triangulation | enabled fix 1 of the LC regression; taking only long baselines costs 143/300 coverage |
| 3 | Two fixed anchors in local BA | removes the exact scale null direction; ~10 %/run window shrink eliminated |
| 4 | Soft-L1 / Huber robust costs throughout | BA 0.95 → 0.93 px per run, sustained across 29–39 runs |
| 5 | Windowed local BA | ATE 0.061 → **0.026 m** (corridor), 0.277 → **0.204 m** (desk) |
| 6 | Constant-velocity prior placing search windows (never as a PnP seed) | prevents the prediction from reproducing itself; tracker stalls without the distinction |
| 7 | Motion-adaptive search radius + tightening ratio test | survives 4–8°/frame pans that a fixed 14 px window loses |
| 8 | Map-point descriptor refresh from the newest view | without it, tracked count decays ~30 %/frame after ~10 frames of viewpoint change |
| 9 | tf-idf BoW + inverted index + relative score bar + covisibility consistency | 43 candidates proposed, 1 accepted on the loop clip; 4 proposed, 0 accepted on the corridor |
| 10 | Sim(3) verification against the candidate's **local map** | 25 → **167** inliers on the true closure |
| 11 | Guided-support re-match through the estimated Sim(3) | stopped the corridor false positive |
| 12 | Sim(3) pose-graph optimisation + covisibility edges | loop residual **2.80 → 0.228, −91.9 %** |
| 13 | Acceptance guards + rollback | 96–100 % reduction for real closures vs 32 % for the false one — the guard's discriminating power |
| 14 | Structure-only BA after closure | restores map/pose consistency without letting BA undo the closure |
| — | *Hartley–Sturm optimal triangulation* | **rejected**: +12.57 % vs +12.66 % bias. Not the triangulator's fault |
| — | *Inverse-depth BA parameterisation* | **rejected**: scale drift is a null direction of BA, not an ill-conditioned one |
