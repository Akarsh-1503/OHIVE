# ADR-0005 — Estimating intrinsics from the video, with no calibration

**Status:** accepted · **Date:** 2026-09 · **Code:** `slam/tracking.py::refine_focal`, `pipeline.py::_refine_focal`

## Context

The product accepts an arbitrary uploaded video. It has no calibration file, no checkerboard
session, and no reliable EXIF — consumer video containers rarely carry focal length, and when
they do it is in 35 mm-equivalent terms that need a sensor size nobody supplies.

But SLAM needs `K`. Every stage depends on it: the essential matrix, triangulation, PnP,
bundle adjustment's projection, the pose graph's residual. A wrong `K` does not add noise — it
adds a *systematic* distortion, because focal length and depth trade off directly in the
perspective division.

Something has to produce a focal length.

## Options

### A. Require the user to supply a calibration

Correct, and unacceptable as a product. "Upload a video" becomes "print a checkerboard, shoot
40 images of it, run a calibration script, then upload a video." It would also make the
one-click sample demo the *only* thing a reviewer could realistically run.

### B. Assume a fixed default and never estimate

`f = 0.8 × width` (≈64° HFOV) is a reasonable middle for phones (~0.85) and webcams (~0.7).
Simple, and wrong by up to ±30 % on any given camera, permanently, with no way to know.

### C. Full self-calibration — estimate `fx`, `fy`, `cx`, `cy` and distortion from the video

Over-parameterised for the data available. Principal point is notoriously weakly observable
from a short handheld sequence, and estimating it alongside focal makes both worse. Distortion
from an uncalibrated sequence is worse still.

### D. Constrained self-calibration in two stages · **chosen**

Assume the priors that are almost always true — square pixels (`fx = fy`), zero skew,
principal point at the image centre — and estimate the one remaining parameter, `f`, twice.

## Decision

**D**, in two stages.

### Stage 1 — Sturm/Bougnoux from the two-view fundamental matrix

A true essential matrix has singular values `(σ, σ, 0)`. So sweep `f` across
`[0.55·w, 1.45·w]` in 13 steps, form `K = diag(f, f, 1)` with a centred principal point, take
the SVD of `KᵀFK`, and score the normalised singular-value gap:

```
data(f)  = |σ₀ − σ₁| / (σ₀ + σ₁)
score(f) = data(f) + λ · log²(f / f_prior),   λ = 0.02,  f_prior = 0.8·w
```

Thirteen 3×3 SVDs. Essentially free.

**The prior term is the interesting part.** `data(f)` is close to **flat** whenever the motion
is rotation-dominated — which handheld video very often is — and a flat data term biases long.
A weak log-normal MAP prior (`λ = 0.02`) is small enough that it only decides the answer when
the data genuinely cannot, and it keeps the estimate inside the range consumer cameras
actually occupy.

### Stage 2 — one bundle adjustment with `fx = fy` free

Two-view self-calibration only ever gets `f` to about ±15 %. Once six keyframes of real 3D
structure exist, BA is far better conditioned for this. At keyframe 6, one wide-window BA runs
with the focal free, then it is frozen again.

The focal is carried as **`log f`**, so the step is scale-relative and `f` can never go
negative:

```
∂(u,v)/∂(log f) = f · (x/z, y/z)
```

Two details that matter:

- The result is **range-checked** (`0.55·w < f < 1.45·w`) before it is accepted. A BA that
  wanders outside the plausible range is rejected rather than trusted.
- That run is **excluded** from the reported `ba_before_px` / `ba_after_px`. It starts from a
  knowingly wrong focal, so folding it in would flatter the local-BA figure by several
  hundredths of a pixel. It is reported separately as `focal_ba_px`.

### Escape hatch

`SlamConfig(fx=…, fy=…, cx=…, cy=…)` bypasses the search entirely and is honoured verbatim —
no search, no BA refinement, no prior. There is a test for it
(`test_calibration_override_is_honoured`).

## Consequences

**Measured against exact synthetic ground truth** (`f = 900` at 1280 px → **450** at the 640 px
tracking width):

| clip | recovered `f` | error |
|---|---|---|
| `synthetic_corridor` | 446.0 px | **−0.9 %** |
| `synthetic_loop` | 501.8 px | **+11.5 %** |

The corridor is essentially perfect. The loop is over-estimated by 11.5 %, and that error is
**the largest single contributor to its 0.711 m ATE** — a wrong focal produces a
systematically distorted trajectory, and Sim(3) alignment can absorb a similarity but not a
shape distortion. The corridor's 0.026 m at −0.9 % focal error is the control that demonstrates
this.

So the cost of this decision is quantified rather than hand-waved: **roughly an order of
magnitude in ATE, on a clip where self-calibration goes wrong.**

**Why the loop clip is the hard one:** it is an elliptical path with continuous yaw — a
rotation-dominated motion, which is exactly the case where `data(f)` flattens. The corridor is
a straight translation, which constrains `f` well. The failure is not random; it is the known
degeneracy, occurring where theory says it should.

**What is deliberately not estimated:**

- **Principal point.** Assumed at the image centre. Weakly observable, and estimating it makes
  the focal estimate worse.
- **Distortion.** Assumed zero. This is a real gap — the TUM clip ships published
  radial-tangential coefficients with `k1 = 0.26`, substantial barrel distortion, and they are
  **not applied**. See [07 §6](../07-limitations.md#6-no-lens-distortion-model-at-all).
- **Per-frame focal.** Assumed constant. Autofocus breathing is not modelled.

**The gap that should be closed first:** `SlamConfig` accepts a calibration and the HTTP API
does not expose it. A reviewer with a calibrated camera cannot use their calibration through
the product. Adding `fx`/`fy`/`cx`/`cy` as optional form fields on `POST /jobs` is a small
change and would remove this ADR's cost entirely for anyone who has the data.

**Reported, not hidden.** `report.json` and the `Reconstruction` carry
`intrinsics.source: "estimated" | "user"` and the recovered values, so a consumer of the
output always knows whether the geometry rests on a measurement or an inference.
