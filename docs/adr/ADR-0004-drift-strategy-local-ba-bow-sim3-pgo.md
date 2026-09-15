# ADR-0004 — The drift strategy, and why global BA was rejected

**Status:** accepted · **Date:** 2026-09 · **Answers:** the brief's requirement #4

## Context

The brief asks for "an approach to minimise accumulated pose error and drift." Three distinct
errors accumulate in a monocular system and they do not have the same answer:

| | fixed by |
|---|---|
| rotational drift | robust costs, windowed BA, more parallax |
| translational drift | the above, plus loop closure |
| **scale drift** | **only an external constraint** |

The third is the one that makes this decision non-obvious, and the reason is worth stating
before the options, because it eliminates most of them.

**Scale drift is invisible to bundle adjustment.** Scale a monocular reconstruction —
`X_j → s·X_j`, `t_i → s·t_i`, rotations unchanged — and every reprojection residual is
*bit-identical*, because `s` cancels in the perspective division. A uniformly rescaled map sits
at exactly the same point on the cost surface: a **zero-curvature null direction** of the
Hessian, not a shallow valley. Derivation in
[03 §7](../03-drift-mitigation.md#7-the-negative-results).

So no amount of optimiser — local BA, global BA, a better point parameterisation, a better
solver — can observe accumulated scale error. Only an external constraint can, and in a pure
monocular system the only one available is a revisited place.

## Options

### A. Local BA only

Cheap, and it does hold reprojection error down. But it cannot see scale drift (above), and it
cannot correct a loop, because a windowed optimiser never sees both ends of one.

### B. Local BA + full global BA at the end

The intuitive "optimise everything at the end" answer.

**Rejected on two grounds.**

*Latency.* Global BA over 24–39 keyframes and 2200–4100 points is a 10 000–13 000 parameter
problem. It is implemented (`mapping.py::global_bundle_adjust`, reachable via
`enable_global_ba`) and it does not fit: optimisation is already 25–52 % of wall clock with
only the 7-keyframe window.

*It does not do the job.* Even given unlimited time, global BA cannot remove scale drift. It
would lower reprojection error and leave the trajectory's accumulated scale error exactly
where it was. Spending the entire remaining budget on the metric that is already fine, while
the metric that is broken is provably invisible to it, is the wrong trade.

### C. Loop closure with an **SE(3)** pose graph

Correct machinery for rotational and translational drift; structurally unable to represent
scale error, because an SE(3) edge has no scale component. On a monocular loop, an SE(3)
closure forces two differently-scaled halves of a map to agree on position, which distorts
both.

### D. Local BA + Huber + BoW detection + **Sim(3)** pose-graph optimisation · **chosen**

## Decision

**D**, as a layered strategy where each layer does what the layer below cannot:

| layer | what it fixes | cost |
|---|---|---|
| Parallax gates + depth cap + longest-baseline-first triangulation | the *source* of scale drift, before any optimiser runs | ~0 — it is a choice of which pairs to use |
| Robust costs (soft-L1 / Huber / RANSAC) at every stage | outlier contamination of everything downstream | ~0 |
| **Windowed local BA**, 7 keyframes free, ≥ 2 fixed anchors | local reprojection error, local geometric consistency | 25–52 % of wall |
| **tf-idf BoW place recognition** + candidate filtering | *finding* the external constraint | ~0 (one bincount per keyframe) |
| **Sim(3) RANSAC verification** vs the candidate's local map + guided support | not acting on a false one | small, per candidate |
| **Sim(3) pose-graph optimisation** with covisibility edges | accumulated rotational, translational **and scale** drift | one solve per accepted closure |
| Acceptance guards + rollback | a wrong closure being worse than none | ~0 |
| Structure-only BA afterwards | map/pose consistency the closure broke | one pass at the end |

Full mechanism-by-mechanism treatment with before/after numbers:
[03 — Drift mitigation](../03-drift-mitigation.md).

## Why each sub-choice, briefly

**`Sim(3)`, not `SE(3)`** — because the whole point is to observe the scale ratio the
trajectory has accumulated. The verification step recovers it via Umeyama with scale; the
graph carries it as `σ` in the tangent vector.

**`Ω = I`, including the scale row** (`pgo_scale_information = 1.0`). Identity information lets
the accumulated scale error redistribute around the *whole* loop rather than collapsing onto
the closure edge. This is ORB-SLAM's choice, and it is a deliberate one, not a default left
unexamined.

**Covisibility edges beside odometry edges** (any pair sharing ≥ 80 points) — they make the
graph rigid, so the correction spreads rather than concentrating at the seam.

**The gauge is fixed by omitting the reference vertex from the system**, not by adding a
prior. That keeps the normal matrix non-singular without inventing an information matrix
nobody measured.

**Structure-only BA after a closure, poses fixed.** PGO has just used information that BA
cannot see. Re-opening the poses lets BA trade that information away for a marginally lower
reprojection error — a strictly bad trade that looks like an improvement on the metric BA
optimises.

## Consequences

**Measured** (dev machine, median of 5, `bench/results.json`):

| | |
|---|---|
| Loop residual on the accepted closure | **2.80 → 0.228, −91.9 %**, 167 Sim(3) inliers |
| `synthetic_loop` ATE | 0.718 m (LC off) → **0.711 m** (LC on) |
| `synthetic_corridor` ATE | **0.026 m**, 0 closures — correctly, no revisit exists |
| `desk_handheld_tum` ATE | 0.168 m (LC off) → 0.204 m (LC on) — **worse**, see below |
| Local BA ablation | corridor 0.026 → 0.061 m; desk 0.204 → 0.277 m |

**This strategy had to be debugged, not just designed.** An earlier iteration had loop closure
making ATE *worse* — 1.140 m with, 0.781 m without. Three changes fixed it: longest-baseline-
first triangulation, verifying against the candidate's **local map** rather than a single
keyframe (25 → 167 inliers on the true closure), and the two acceptance guards with rollback.
The full narrative is [03 §5](../03-drift-mitigation.md#5-loop-closure--and-the-regression-that-made-it-work).

**Known weakness of this decision:** on a *fragmented* trajectory — the real handheld clip,
which tracks 144/300 frames at defaults — a closure spanning two disconnected arcs applies a
global scale correction that one arc did not need, and ATE gets slightly worse (0.168 →
0.204 m). The system has no "is my trajectory one connected piece?" test before closing, and
it should. [07 §10](../07-limitations.md#10-loop-closure-is-a-net-negative-when-the-trajectory-is-already-fragmented).

**Global BA remains implemented and off** (`enable_global_ba=False`), and the deployment-host
measurement has since settled that question in the other direction: the host already misses the
10 s budget on two of three clips
([06 — Performance](../06-performance.md#deployment-host--measured-and-it-does-not-meet-the-10-s-budget)),
so there is no headroom to spend. It would not have changed the drift numbers anyway, for the
reason this ADR opens with.
