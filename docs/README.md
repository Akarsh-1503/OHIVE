# Driftless documentation

**Live: https://slam-34-47-153-95.nip.io** · Start with the [project README](../README.md)
if you have not read it — it has the quickstart and the requirements-traceability table.

Every number in these documents is measured, and the command that produced it is named next
to it. Where a figure could not be measured it is labelled **projected** or **estimated** in
the text and never presented as a measurement. Diagrams are Mermaid, so they render in any
Git host with no binary assets.

> **The map is up to scale.** A single moving camera cannot observe absolute distance. The
> initial two-view baseline is fixed to 1.0 and every length in this documentation — point
> coordinates, trajectory length, ATE, the drift figures — is in those units. The clips used
> here have metric ground truth, so a Sim(3) alignment is applied before any error is
> reported; that is why the errors are quoted in metres at all.

## 60-second tour

Driftless takes a single-lens RGB video, reconstructs a sparse 3D point cloud and the camera
trajectory that produced it, and renders both in the browser while the reconstruction is
still running.

Three tiers, one box: a Next.js UI, a FastAPI service that owns admission, queueing and
artefacts, and a pure-Python SLAM engine whose only runtime dependencies are `numpy`,
`opencv-python-headless` and `scipy`. No GPU, no native build, no pretrained model.

The pipeline is classical sparse feature SLAM, all four classical stages present and none of
them a stub: grid-bucketed ORB → homography-vs-essential initialisation → constant-velocity
tracking with `solvePnPRansac` against a projected local map → sliding-window local bundle
adjustment with an analytic Jacobian → tf-idf bag-of-visual-words place recognition → Sim(3)
RANSAC verification → Sim(3) pose-graph optimisation.

**The one idea worth taking away:** *scale drift is invisible to bundle adjustment.* Rescale
a monocular map and its trajectory by the same factor and every reprojection residual is
bit-identical, so no amount of BA — local, global, inverse-depth-parameterised — can see the
error. Only an external constraint can, and in a monocular system the only external
constraint available is a revisited place. That is why this system's drift strategy is
organised around loop closure with a Sim(3) (not SE(3)) similarity, and why the
counterexample clip that never revisits is shipped alongside the one that does.

If you read one document, read [**02 — the SLAM algorithm**](02-slam-algorithm.md). If you
read two, read [**03 — drift mitigation**](03-drift-mitigation.md), which answers the brief's
requirement #4 directly. If you read three, read [**07 — limitations**](07-limitations.md).

> ### One number you should not have to hunt for
> **Requirement #6 — "a 10-second video in ≤10 seconds in the demonstrated test environment."**
> Measured against the live URL on a `c3-standard-4`: `office_handheld` (real TUM fr3 footage)
> **9.6 s** and `synthetic_corridor` **7.8 s** are inside the budget; `synthetic_loop`
> **11.3 s** and `desk_handheld` (real TUM fr1) **15.2 s** are over. On the larger
> `c3-standard-8` the loop clip ran **9.7 s**, inside budget — the host was sized down to halve
> running cost, which is a deployment choice, not an engine limit.
>
> The handheld clip is the interesting one. 151 frames of rotational motion blur drive the
> tracker to 49 keyframes against 24–30, and bundle adjustment runs once per keyframe — those
> keyframes cost 6.2 s of optimisation and buy the **best drift result in the set** (99.8 %
> loop-error reduction). One knob change (`ba_max_nfev` 20 → 12) closed the gap on
> `synthetic_loop` while improving accuracy; every remaining knob was measured and rejected for
> costing trajectory coverage or accuracy, one of them caught by a test asserting pose coverage
> ≥ 250. What is left is a **cost decision** — a higher-clock instance closes it with no code or
> accuracy change — and it was deferred rather than bought. Reproduction is one `curl` loop; the
> measurements and the rejection table are here:
> [06 — Performance](06-performance.md#what-would-close-the-gap)
> and [07 §9](07-limitations.md#9-the-10-s-budget-holds-on-the-10-second-clips-the-real-handheld-clip-needs-more-machine).

## Index

| | |
|---|---|
| [01 — Architecture](01-architecture.md) | System diagram, job lifecycle, data flow, process model, deployment topology, and the AWS equivalent of every GCP resource |
| [02 — SLAM algorithm](02-slam-algorithm.md) | **The technical centrepiece.** The full pipeline with the mathematics stated properly: SE(3)/Sim(3) on the Lie algebra, the BA residual and its analytic Jacobian, Umeyama, tf-idf scoring |
| [03 — Drift mitigation](03-drift-mitigation.md) | **Requirement #4.** Every mechanism that fights accumulated pose error, with before/after numbers — including the two that did not work and why |
| [04 — API](04-api.md) | Every endpoint with a real `curl` and a real response |
| [05 — Frontend](05-frontend.md) | UI architecture, the SSE hook, the WebGL viewer's render-avoidance strategy, the motion rationale |
| [06 — Performance](06-performance.md) | **Requirement #6.** Benchmark methodology precise enough to re-run, the exact scope of `wall_ms`, measured numbers, test environment |
| [07 — Limitations](07-limitations.md) | The failure modes, named. Including the ones that are embarrassing |
| [08 — Security & cost](08-security-and-cost.md) | Threat model of a public demo with no authentication, abuse limits, what it costs to run |

## Decision records

One per genuinely contested fork in the road — not one per file.

| | |
|---|---|
| [ADR-0001](adr/ADR-0001-classical-sparse-slam-over-learned-methods.md) | Classical sparse feature SLAM over DROID-SLAM / MASt3R: the 10 s-per-10 s CPU budget decides it |
| [ADR-0002](adr/ADR-0002-orb-over-sift-and-superpoint.md) | ORB over SIFT / SuperPoint: binary descriptors, Hamming matching, licence, speed |
| [ADR-0003](adr/ADR-0003-homography-vs-essential-model-selection.md) | Homography-vs-essential model selection at initialisation, and the planar-scene degeneracy it exists for |
| [ADR-0004](adr/ADR-0004-drift-strategy-local-ba-bow-sim3-pgo.md) | The drift strategy: windowed local BA + robust loss + BoW detection + Sim(3) PGO, and why global BA was rejected |
| [ADR-0005](adr/ADR-0005-intrinsics-estimation-without-calibration.md) | Estimating intrinsics from the video itself, and what that costs in accuracy |
| [ADR-0006](adr/ADR-0006-process-pool-and-single-concurrent-job.md) | A process pool with exactly one concurrent job: protecting the benchmark claim |
| [ADR-0007](adr/ADR-0007-flat-typed-arrays-over-object-per-point-json.md) | Flat typed arrays + gzip over a naive object-per-point JSON payload |

## Related reading in the repository

| | |
|---|---|
| [`../slam/README.md`](../slam/README.md) | The SLAM engine as a standalone library: install, API, the robustness/latency knob |
| [`../backend/README.md`](../backend/README.md) | Backend service reference, including the authoritative definition of `wall_ms` |
| [`../frontend/README.md`](../frontend/README.md) | Frontend reference, including the in-browser mock backend and the viewer frame-rate benchmark |
| [`../samples/README.md`](../samples/README.md) | The three ground-truth demo clips, how they were generated, and the TUM RGB-D attribution |
| [`../bench/results.json`](../bench/results.json) | Raw benchmark output — every individual run, not just the medians |
| [`../../infra/README.md`](../../infra/README.md) | Public deployment runbook for the live host |
| [`../../infra/COST.md`](../../infra/COST.md) | What the live host costs, from the Cloud Billing Catalog API |
| [`../../_contracts/assignment2-api.md`](../../_contracts/assignment2-api.md) | The frozen API contract both tiers implement |
