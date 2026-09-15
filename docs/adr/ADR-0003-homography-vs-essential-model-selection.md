# ADR-0003 — Homography-vs-essential model selection at initialisation

**Status:** accepted · **Date:** 2026-09 · **Code:** `slam/tracking.py::Initializer`

## Context

Monocular SLAM has to bootstrap a map from two views before anything else can happen. The
standard route is: match features, estimate the essential matrix `E` with RANSAC, decompose it
into `(R, t)`, triangulate, done.

That route has a **structural degeneracy**, not a numerical one. If the observed scene is
dominated by a single plane, the correspondences are explained exactly by a homography, and
the epipolar constraint is under-determined: there is a one-parameter family of `E` consistent
with the data. RANSAC will happily return one of them with a healthy inlier count, and
`recoverPose` will return a plausible-looking `(R, t)`.

The result is a map that is wrong from frame one, in a way that no downstream optimiser can
repair — bundle adjustment will converge nicely to a locally consistent, globally incorrect
reconstruction, and drift mitigation then spends the rest of the sequence compensating for a
bad gauge.

This is not a rare case. Planar-dominant scenes are: a camera facing a wall, a desk surface, a
floor, a whiteboard, a building façade, the start of most indoor handheld clips.

## Options

### A. Essential only

Simple, and what most tutorials do. Silently wrong on planar scenes.

### B. Homography only

Correct on planes; degenerate on general 3D scenes, where the homography does not explain the
data and `decomposeHomographyMat` returns four solutions, none of them right.

### C. Try both, keep whichever triangulates more points

Tempting, and wrong. On a planar scene the *essential* decomposition often triangulates more
points — they are just in the wrong places. Point count is not a measure of correctness.

### D. Score both models on a common statistical footing, choose by score ratio · **chosen**

ORB-SLAM's approach.

## Decision

**D.** Fit both `H` (RANSAC, 2.5 px) and `F` (RANSAC, 1.0 px), score both with a χ²-gated
symmetric transfer error, and select on the ratio.

For the homography, symmetric transfer error with the 2-DoF χ² threshold `5.991`:

```
S_H = Σᵢ [ ρ(5.991 − ‖p₂ᵢ − H p₁ᵢ‖²/σ²) + ρ(5.991 − ‖p₁ᵢ − H⁻¹ p₂ᵢ‖²/σ²) ]
```

For the fundamental matrix, symmetric epipolar distance with the 1-DoF threshold `3.841`:

```
d²(p₂, Fp₁) = (p₂ᵀFp₁)² / ((Fp₁)₁² + (Fp₁)₂²)
S_F = Σᵢ [ ρ(· − d²(p₂ᵢ, Fp₁ᵢ)) + ρ(· − d²(p₁ᵢ, Fᵀp₂ᵢ)) ]
```

with `ρ(x) = max(x, 0)`. Selector:

```
R_H = S_H / (S_H + S_F)
R_H > 0.45  →  homography path
otherwise   →  essential path
```

The two thresholds differ because the residuals have different dimensionality: a point-to-point
transfer error is 2-DoF, a point-to-line epipolar distance is 1-DoF. Using one threshold for
both — a natural-looking simplification — systematically favours whichever model got the
wrong gate.

## Consequences

**Both paths are normalised to a unit baseline before triangulating.** This is the detail that
is easy to miss and expensive to get wrong: `cv2.recoverPose` already returns `‖t‖ = 1`, but
`cv2.decomposeHomographyMat` returns a translation scaled by the plane distance. Without
explicit normalisation the two paths would produce maps in **different gauges**, and every
downstream threshold expressed "relative to scene scale" would silently mean two different
things depending on which branch ran.

**The homography path evaluates all four decompositions** and keeps whichever survives the
cheirality, parallax and reprojection gates with the most points. There is no shortcut here.

**This is not theoretical on the bundled corpus.** `synthetic_loop` initialises through the
**homography** path — `report.json` records `"init_model": "homography"` — because the camera
starts facing a large flat wall section of the room. `synthetic_corridor` initialises through
the essential path. A system without this selection would produce a bad initial gauge on one
of the two synthetic benchmark clips, and its drift numbers would be measuring the wrong
thing.

**Cost:** one extra `findHomography` RANSAC and two scoring passes at initialisation only — a
few milliseconds, once per clip. Nothing in the per-frame budget.

**Interaction with focal search.** When no calibration is supplied, the initialiser evaluates
the self-calibrated focal *and* the prior fallback, running the full model-selection and
triangulation path for each and keeping whichever produces more surviving points. That doubles
the initialisation cost and is still negligible, and it makes initialisation robust to the
focal estimate being off — which, per [ADR-0005](ADR-0005-intrinsics-estimation-without-calibration.md),
it sometimes is by 11 %.

**Residual risk:** the `0.45` threshold is inherited from ORB-SLAM and has not been re-tuned
against this corpus. A scene sitting exactly on the boundary — partially planar — could select
either way run to run under RANSAC noise. No such case has been observed on the three bundled
clips, and it has not been measured beyond that.
