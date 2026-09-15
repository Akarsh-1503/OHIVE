# ADR-0001 — Classical sparse feature SLAM over learned or dense methods

**Status:** accepted · **Date:** 2026-09 · **Decides:** the whole shape of the system

## Context

The brief asks for a single-lens RGB sparse point-cloud SLAM tool, deployed publicly, that
processes a 10-second video in **≤10 seconds in the demonstrated test environment**. The
demonstrated environment is a general-purpose cloud VM: GCE `c3-standard-8`, 8 vCPU from
**4 physical cores**, **no GPU**.

Those two facts together — 10 s of wall clock, no GPU — decide this ADR almost on their own,
but the alternatives deserve to be taken seriously rather than dismissed.

## Options considered

### A. Learned dense/semi-dense SLAM — DROID-SLAM and relatives

State of the art for accuracy on monocular benchmarks by a wide margin. DROID-SLAM's recurrent
update operator over a dense flow field is genuinely better than anything classical at
handling low texture, motion blur and fast rotation — exactly the failure modes this system
has ([07 §1](../07-limitations.md), [07 §4](../07-limitations.md#4-degenerate-motions)).

**Rejected because it needs a GPU, and that is not a tuning problem.** DROID-SLAM's published
throughput is a handful of frames per second on a *high-end* GPU. On 4 CPU cores it is two to
three orders of magnitude short of 30 fps. There is no configuration of it that fits a 10 s
budget on this host.

Adding a GPU to the deployment would mean a GPU VM (≈10–20× the cost of the current box,
running continuously since SLAM cannot scale to zero within a 10 s budget), a ~2 GB model
checkpoint in the image, and a CUDA toolchain. It would also make the submission's most
interesting property — that it runs anywhere Docker runs, with three Python dependencies —
disappear.

### B. Learned two-view reconstruction — MASt3R / DUSt3R

Impressive, and genuinely different: direct pointmap regression from image pairs with no
explicit feature matching. But it is a *reconstruction* method, not a SLAM system. Turning it
into one means a global alignment optimisation over all pairs, which scales badly with
sequence length, and it also needs a GPU. Same wall.

### C. Direct / semi-dense classical — LSD-SLAM, DSO

No feature extraction; optimise photometric error directly over high-gradient pixels. Produces
a denser map than ORB-SLAM at comparable cost, and DSO in particular is very accurate.

**Rejected on two counts.** First, the brief explicitly asks for a *sparse point cloud*,
which is what feature-based SLAM natively produces. Second, and more decisive: direct methods
assume photometric consistency, which requires either a photometrically calibrated camera or
an explicit exposure model. An arbitrary uploaded video has auto-exposure and auto-white-
balance doing unmodelled things frame to frame. Feature-based methods are invariant to that
by construction.

There is also a practical point: a competent direct implementation needs a fast image-pyramid
warp and a windowed photometric bundle adjustment over thousands of points per keyframe. In
pure Python over NumPy that is far harder to make fast than the sparse algebra a feature
method needs.

### D. Classical sparse feature SLAM — the ORB-SLAM lineage · **chosen**

ORB features, PnP tracking, keyframes, windowed bundle adjustment, bag-of-words loop closure,
Sim(3) pose-graph optimisation.

## Decision

**D.** Build a classical sparse feature SLAM system on `numpy`, `opencv-python-headless` and
`scipy`.

## Consequences

### What this buys

- **It fits the budget — on the dev machine.** Measured: 2.76–4.76 s for a 10 s clip at 640 px,
  2.1–3.6× realtime. On the deployment host it is 7.0 s, 11.9 s and 12.5 s, so two of three
  clips are over. That is a *tuning* shortfall within a workable design
  ([06 — Performance](../06-performance.md#deployment-host--measured-and-it-does-not-meet-the-10-s-budget)),
  which is a categorically different position from options A and B, where the gap is two to
  three orders of magnitude and no amount of tuning closes it.
- **Three runtime dependencies.** No GPU, no CUDA, no model download, no native build, no
  network egress at runtime. The image builds offline and starts in under a second.
- **Every stage is inspectable.** The reason [03 — Drift mitigation](../03-drift-mitigation.md)
  can say *why* a number moved — and the reason two failed experiments could be diagnosed
  rather than just observed — is that there is no learned component to shrug at.
- **A sparse point cloud is the native output**, which is what the brief asked for.
- **Marginal cost per reconstruction is ≈$0.001**, versus a continuously-running GPU.

### What it costs

- **Accuracy.** A learned method with a GPU would beat this on ATE, particularly on the real
  handheld clip. That is not arguable and it is not hidden.
- **The failure modes are the classical ones**, all of them present and all named in
  [07 — Limitations](../07-limitations.md): low texture, pure rotation, fast pans, motion
  blur, no metric scale.
- **Pure Python is a real constraint.** It shapes the implementation everywhere — the
  struct-of-arrays map, `cv2.batchDistance` instead of `BFMatcher`, uint8 geometric masks
  handed to OpenCV so the `O(MN)` loop stays in C++, the analytic BA Jacobian to avoid ~45
  finite-difference residual evaluations per step. Those are not micro-optimisations; without
  them the budget is missed.

### The honest framing

This is not "classical is better than learned". It is: **given a hard latency budget on CPU-
only commodity hardware, the classical method is the only one that runs at all**, and inside
that constraint the interesting engineering is drift, not feature extraction. A different
brief — offline processing, or a GPU in the deployment — would flip this decision.
