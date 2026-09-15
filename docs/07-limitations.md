# 07 — Limitations

Written to be uncomfortable. Every item is a real, reproduced failure of the system as
shipped, with the measurement that shows it. Nothing here is hypothetical, and nothing that
is known to be broken has been left out to make the rest look better.

Ordered by how much they would matter to someone actually *using* this.

> **If you read one item, read [§9 — the 10 s budget and the handheld
> clip](#9-the-10-s-budget-holds-on-the-10-second-clips-the-real-handheld-clip-needs-more-machine).**
> It is last in this ordering because it is an operational constraint rather than a
> user-visible defect, but it is the one item that qualifies a headline claim of the
> submission, and it is reproducible against the live URL in thirty seconds.

---

## 1. On the real handheld clip, 144 of 300 frames are not tracked at all

This is the largest single weakness in the submission.

`samples/desk_handheld_tum.mp4` is 300 frames of real handheld video from the TUM RGB-D
benchmark. At shipped defaults the tracker places **144 of them**. Measured:

```
tracked                             144 / 300
longest contiguous untracked run    150 frames, starting at frame 88
lost_events                         149
```

The cause is specific and measurable. Per-frame camera rotation in that clip, from the 100 Hz
motion-capture ground truth:

| | |
|---|---|
| median | 1.20 °/frame |
| 95th percentile | 2.68 °/frame |
| **maximum** | **7.63 °/frame, at frame 98** |

At 7.63°/frame with `f ≈ 513 px`, features move ~68 px between frames. The tracker's
motion-adaptive window grows to a 84 px cap, and the ratio test tightens as it grows — that
combination is what recovers *most* fast motion — but through this particular pan the existing
map rotates out of view faster than new structure can be triangulated into it, relocalisation
finds no BoW match because no keyframe has seen the new viewpoint yet, and tracking does not
recover until frame 238.

**There is a setting that fixes it, and it is not the default:**

```python
SlamConfig(kf_min_parallax_ratio=0.052)   # ≈3° instead of ≈7.4°
```

| setting | tracked frames | wall | ATE |
|---|---|---|---|
| default (`0.13`) | 144 / 300 | 4.3 s | 0.204 m |
| robust (`0.052`) | **286 / 300** | **10.1 s** | 0.282 m |

It roughly doubles the keyframe count, which is exactly what keeps tracking alive through the
pan. It is not the default for two reasons, both measured:

1. **10.1 s does not fit the 10 s budget** on the dev machine, let alone the slower deployment
   host. Requirement #6 and requirement #3 are in direct conflict on this clip, and the
   submission chose the one the brief states as a hard number.
2. **It makes place recognition less selective.** At `0.052` the system produced **two false
   loop closures on `synthetic_corridor`**, a clip that by construction never revisits a
   viewpoint. Denser keyframes mean more near-duplicate BoW vectors and a lower effective
   score bar. Trading a correct "no closure" for two wrong ones is a bad trade.

The `width-480` ablation shows the same conflict from another angle: **294/300 tracked frames
and ATE 0.078 m**, the best accuracy any configuration achieves on this clip — at 6.66 s, and
with a 2.9× denser map that pushes optimisation from 1266 ms to 3226 ms.

**What I would actually do with more time:** make the keyframe policy adaptive rather than
fixed — drop `kf_min_parallax_ratio` only while the predicted inter-frame rotation exceeds
~4°/frame, and restore it afterwards. That buys the robustness exactly where it is needed and
pays the latency only for the handful of frames that need it. It is not implemented, and
claiming a configuration knob is the same thing as an adaptive policy would be dishonest.

---

## 2. Depth is inflated 4–5× by low-parallax triangulation, and it is not fixed

Two-view triangulation has a systematic **bias**, not just noise: depth ∝ 1/disparity,
disparity is the noisy quantity, and `E[1/x] > 1/E[x]`. Measured on a synthetic bench at
0.5 px keypoint noise:

| parallax | depth bias |
|---|---|
| 0.6° | +12.6 % |
| 1.4° | +1.3 % |
| 5.7° | +0.01 % |

Four parallax gates and a `3 × ref_depth` cap ([03 §1](03-drift-mitigation.md)) hold this down
— they took end-of-loop scale inflation from 7.4× to ~1.1× — but points triangulated near the
2.5° floor still sit **4–5× too far away**. Those points are in the published cloud.

The obvious fix does not work. Hartley–Sturm optimal triangulation, which minimises the true
geometric error instead of an algebraic one, measures **+12.57 % bias against DLT's +12.66 %**
— identical within noise, because the bias comes from the parameterisation and the noise
model, not from the objective function. It was implemented, measured, and **not adopted**.
Details in [03 §7](03-drift-mitigation.md#7-the-negative-results).

The only real fix is more parallax, which costs latency (item 1) or a multi-view triangulator
with proper uncertainty propagation, which costs implementation time neither of which was
spent.

---

## 3. There is no metric scale, and scale drifts

**Ambiguity.** A single moving camera cannot observe absolute distance — it is a property of
the sensor, not of this implementation. The initial two-view baseline is fixed to 1.0 and
every length in the output is in those units. `trajectory_length_m` is named `_m` because the
frozen API contract names it that; it is **not metres**. The UI says so on the landing page,
in the metrics panel and under the viewer, and the PLY export carries
`comment Driftless monocular SLAM, reconstruction is up to scale` so the file still says it
when it is opened in a mesh viewer three weeks later.

**Drift.** Worse than the ambiguity: the unit of length changes *along* the trajectory. On
`synthetic_corridor`, which never revisits a viewpoint, the recovered Sim(3) scale factor
against ground truth is **0.55** — the reconstruction is roughly half life size, uniformly.
Its ATE is excellent (0.026 m) because Sim(3) alignment absorbs exactly that factor. Reporting
ATE-after-alignment is the standard and correct monocular protocol, and it is also a metric
that **cannot see** the error this section is about. Both facts are true at once.

Without a revisit there is nothing that can observe accumulated scale error — not local BA,
not global BA, not a better point parameterisation, because a uniformly rescaled map and
trajectory produce **bit-identical** reprojection residuals ([03 §7](03-drift-mitigation.md#7-the-negative-results)).
Anything monocular, uncalibrated and loop-free inherits this.

---

## 4. Degenerate motions

| motion | what happens |
|---|---|
| **Pure rotation** | No baseline ⇒ no triangulation. Initialisation refuses to start (median keypoint displacement < 2 px is an explicit early return). Mid-sequence, tracking survives on existing map points until they rotate out of view, then loses |
| **Purely forward translation** | The epipole sits inside the image. Parallax is near zero near the centre of the frame and points there triangulate badly, exactly where a forward-moving camera looks |
| **Low texture** | ORB yields collapse. There is one mitigation — a retry at `fastThreshold=5` when a frame returns fewer than 600 keypoints — and past that, tracking fails. A blank wall is not recoverable |
| **Dominant plane** | Handled: homography-vs-essential model selection exists for exactly this, and `synthetic_loop` actually initialises through the homography path. Listed here because it is a degeneracy that *would* break a naive implementation |
| **Motion blur** | Not modelled. ORB corners smear; the relaxed-threshold retry finds weaker, less repeatable corners, which is worse than finding none |

The bundled clips do not contain a pure rotation or a textureless stretch. That is a gap in
the test corpus, not evidence of robustness.

---

## 5. Focal self-calibration goes flat exactly when it is needed

The two-view focal estimate is a Sturm/Bougnoux singular-value test: sweep `f`, look for the
`K^T F K` that is closest to a true essential matrix `(σ, σ, 0)`. That data term is **nearly
flat** under rotation-dominated motion and under purely forward motion — which are precisely
the motions consumer handheld video is full of. A flat data term biases long.

The mitigations are a weak log-normal MAP prior (`λ = 0.02`, centred on `0.8 × width`) and one
wide-window BA at keyframe 6 with `fx = fy` free. Neither closes the gap. Measured against
exact synthetic ground truth (`f = 450 px` at the 640 px tracking width):

| clip | recovered `f` | error |
|---|---|---|
| `synthetic_corridor` | 446.0 px | **−0.9 %** |
| `synthetic_loop` | 501.8 px | **+11.5 %** |

A wrong focal does not produce a random trajectory — it produces a *systematically distorted*
one, because focal and depth trade off directly in the projection. Sim(3) alignment cannot
absorb a shape distortion, so it lands entirely in ATE. That is the largest single contributor
to `synthetic_loop`'s 0.711 m, and the corridor's 0.026 m at −0.9 % focal error is the
control that shows it.

Supplying a calibration (`SlamConfig(fx=…, fy=…, cx=…, cy=…)`) bypasses the search entirely
and is honoured verbatim — there is a test for it — but the HTTP API does **not** expose that
as a form field. It should.

---

## 6. No lens distortion model at all

The pinhole model is used unmodified. Distortion coefficients are never estimated and, where
they are known, never applied.

That is not a hypothetical on the bundled corpus. `desk_handheld_tum_gt.json` carries TUM's
published `freiburg1` radial-tangential coefficients:

```
[0.262383, -0.953104, -0.005358, 0.002628, 1.163314]
```

A `k1` of 0.26 is substantial barrel distortion. **It is not undistorted before tracking.**
Straight lines in that clip are not straight in the image, the epipolar geometry is
consequently mis-modelled, and some fraction of that clip's 1.42 px mean reprojection error —
against 0.53 px on the distortion-free synthetic corridor — is distortion being absorbed as
noise.

Fixing it for the bundled clip is trivial (`cv2.undistort` with known coefficients). Fixing it
for an arbitrary upload is not, because it means estimating distortion alongside focal from
the video itself, in a system whose focal estimate is already the weak link (item 5).

---

## 7. No rolling-shutter model

Every consumer phone camera is rolling-shutter: the top of the frame is exposed milliseconds
before the bottom. Under fast motion a "frame" is not a single camera pose, and treating it as
one biases every pose in a systematic, motion-dependent way — precisely during the fast pans
that already cause item 1.

The bundled synthetic clips are global-shutter by construction and the TUM clip is from a
global-shutter Kinect, so **nothing in the published numbers exposes this**. A reviewer
uploading a phone video is in territory this system has not measured.

---

## 8. Platform-dependent numerics

The reconstruction is **not bit-reproducible across dependency versions or CPU
architectures**. It is deterministic within a fixed stack — repeated runs in the same
environment produce identical keyframe counts, point counts and closures — and different
between stacks.

**Same machine, different pinned versions.** `bench/results.json` was produced with NumPy
2.4.6 / OpenCV 4.14.0. The shipped container pins NumPy ~2.1 / OpenCV ~4.10 and resolves to
2.1.3 / 4.10.0. Same clips, same config, one repeat:

| clip | ATE, benchmark stack (cv2 4.14) | ATE, **shipped** stack (cv2 4.10) |
|---|---|---|
| `synthetic_loop` | 0.711 m | 0.404 m |
| `synthetic_corridor` | 0.026 m | 0.033 m |
| `desk_handheld_tum` | 0.204 m | 0.364 m |

**Different architecture.** The same clip inside Docker Desktop's emulated arm64 Linux VM
produced `scale_drift_ratio` **18.5** and `trajectory_length_m` **341.8**, against 0.9986 and
8.9 natively — the low-parallax scale-inflation failure mode of item 2, triggered by a
different arithmetic path through OpenCV's RANSAC and SVD.

None of this changes the sign of any claim in this submission, and no headline number moves by
more than about a factor of two. But **the published benchmark does not describe the deployed
artefact byte for byte**, and `bench/benchmark.py` records the Python version without
recording the library versions that actually do the arithmetic — which is how the drift went
unnoticed. Both are named in
[06 — Performance](06-performance.md#the-version-discrepancy--worth-fixing), and both are
small fixes that should be made before submission.

Under the shipped stack the loop-closure verification path also emits
`RuntimeWarning: invalid value encountered in matmul` from `loop.py:286` — NaNs reaching the
guided-support projection. They are filtered downstream and the closure still verifies, but a
warning like that is a latent bug, not a cosmetic one.

---

## 9. The 10 s budget holds on the 10-second clips; the real handheld clip needs more machine

This is the most consequential item in this document, because it is a headline claim of the
submission rather than an internal quality issue.

The brief asks for "a 10-second video processed in ≤10 seconds **in the demonstrated test
environment**." The demonstrated environment is the public URL. Measured against it
(`https://slam-34-47-153-95.nip.io`, 2026-09-15, repeated runs, one job at a time,
`SLAM_BACKEND=real`, shipped defaults):

| clip | `wall_ms` | ×realtime | poses | keyframes | |
|---|---|---|---|---|---|
| `office_handheld` (TUM fr3, real) | 9 645 ms | 1.04× | 297/300 | 19 | **inside budget** |
| `synthetic_corridor` | 7 841 ms | 1.28× | 288/300 | 24 | **inside budget** |
| `synthetic_loop` | **11 293 ms** | 0.89× | 231/300 | 30 | **13 % over** |
| `desk_handheld` (TUM fr1, real) | **15 205 ms** | 0.66× | 294/300 | 49 | **52 % over** |

Measured on a **`c3-standard-4`** (4 vCPU / 16 GiB, $5.55/day). On the `c3-standard-8` this was
previously deployed to ($10.58/day), `synthetic_loop` ran **9.7 s — inside budget** and three of
four clips passed. Halving the cores to halve the cost is what moved it over; that is a
deployment-economics choice on a demo box rather than an engine limitation.

Run-to-run spread is under 1 %, so this is stable and reproducible, not a cold-start artefact or
a noisy neighbour. Every clip returns a pose for all 300 frames.

**Why the real clip is the one that fails.** `desk_handheld_tum` contains a contiguous 151-frame
section of fast rotational motion blur (≈7.6°/frame). The tracker responds by inserting
keyframes far more densely — 49, against 24–30 on the synthetic clips — and local bundle
adjustment runs once per keyframe, so `optimize_ms` reaches 6.2 s against 2.1–2.6 s. Those
keyframes are not waste: that clip yields the best drift result in the whole set, 2 loop closures
and **99.8 %** loop-error reduction. The cost of surviving hard motion *is* the latency overrun.

### The next step, and why it was not taken

Two clips originally exceeded the budget. `ba_max_nfev` 20 → 12 brought `synthetic_loop` from
11 853 ms to ~9 730 ms while *improving* reprojection error on all three clips and ATE on two —
a free win, so it ships. Every remaining knob was measured and rejected for costing trajectory
coverage or accuracy elsewhere; the table is in
[06-performance](06-performance.md#what-would-close-the-gap).

That leaves two real options for the handheld clip, and both are **cost decisions rather than
unknowns**:

| option | effect | cost |
|---|---|---|
| **Higher-clock instance** | The pipeline is single-thread-bound. The current host is a `c3-standard-8` at 2.70 GHz base; a C4-class part (Emerald Rapids) is ~30–40 % faster on this workload, which lands 13.8 s at roughly 9–10 s. No code change, no accuracy change. | ~25–30 % more per day on a VM already costing **$10.58/day** |
| **Motion-aware keyframe policy** | Throttle insertion when predicted rotation is high and widen the search radius instead, so the 151 blurred frames stop producing 49 keyframes. Est. 3–4 s. | ~½ day of engineering, plus a real risk of regressing tracking on exactly the section it targets |

Both were scoped and deliberately deferred: the demo VM runs continuously and hosts *both*
assignments, so the instance upgrade is a standing cost increase for a margin that only one of
three clips needs, and the keyframe rework is not something to land untested against a
submission deadline. The engine ships at its measured performance with the trade stated.

One thing worth separating from the timing question: the extra keyframes are **not** a defect in
the reconstruction. That clip produces the best drift result in the set. The policy's real
shortcoming is that it counts keyframes without reasoning about what each is worth — 49 through
a blurred pan buy far less map quality than 49 through clean motion — and fixing that would
improve both latency *and* efficiency. It is the right next piece of work, not a patch.

Anyone can reproduce it in about thirty seconds:

```bash
B=https://slam-34-47-153-95.nip.io/api/v1
J=$(curl -s -X POST $B/jobs/from-sample/synthetic_loop | jq -r .job_id)
until [ "$(curl -s $B/jobs/$J | jq -r .status)" = completed ]; do sleep 1; done
curl -s $B/jobs/$J/export/report.json | jq '.metrics | {wall_ms, realtime_factor}'
```

**The prior projection was 8.6–9.5 s and it was wrong by 25–30 %.** It assumed the same work
running on slower cores. In fact the host does *more* work: it produces 43 keyframes on
`synthetic_loop` against the dev machine's 39, and 34 against 29 on the desk clip, because a
different OpenCV build takes different paths through ORB and RANSAC (item 8). Local BA runs
after every keyframe and its cost is superlinear in window occupancy, so `optimize_ms` on the
loop clip is 5613 ms against 2459 ms — a **2.3×** increase, not a 10 % one.

Nothing has been silently tuned to make this go green. The candidate mitigations, their
expected effect and their measured accuracy cost are tabulated in
[06 — Performance](06-performance.md#what-would-close-the-gap); the most promising untested
one is halving bundle-adjustment frequency, since optimisation is 38–47 % of wall on the host
specifically because the host generates more keyframes. That requires an ablation run on the
host, which has not been done, and the full host ablation
(`bench/results-host.json`) does not yet exist.

---

## 10. Loop closure is a net negative when the trajectory is already fragmented

On `desk_handheld_tum`, ATE is **0.204 m with loop closure and 0.168 m without**. The closure
itself is sound — the loop-consistency residual collapses 4.632 → 0.228, a 95 % reduction —
but on the default settings that clip is two disconnected arcs (item 1). The closure applies a
global scale correction of 0.915 across both, and the arc that did not need it absorbs part of
the correction. The map is also 46 % sparser afterwards (2211 points vs 4064), because the
post-closure reconciliation pass culls observations it cannot explain across the seam.

The correct summary is: **loop closure is a clear win when tracking is continuous, and a small
net negative when the trajectory is already fragmented.** The system has no test for "is my
trajectory one connected piece?" before deciding whether to close, and it should.

---

## 11. Single node, single job, no HA

| | |
|---|---|
| **One VM.** No load balancer, no replica, no failover | A reboot is downtime. `ak-project-stack.service` restarts the stack on boot, which is the entire recovery story |
| **One concurrent reconstruction.** `SLAM_MAX_CONCURRENT_JOBS=1` | Deliberate — a second job halves single-job throughput and invalidates the benchmark ([ADR-0006](adr/ADR-0006-process-pool-and-single-concurrent-job.md)) — but it means the demo serialises under any real traffic. Two reviewers at once and the second waits |
| **Job state is in-process.** No database | An API restart loses the job table. Artefacts survive on disk but are **no longer addressable**: the files are there, the job ids are gone |
| **The janitor is time-based only.** `JOB_TTL_HOURS=24` | There is no disk-pressure trigger. A burst of 200 MB uploads inside one TTL window can fill the 100 GiB disk, and nothing reacts until the clock does |
| **Images are built on the VM**, not pushed to a registry | No image history, so no roll-back target. Acceptable for a demo host, not for production |

---

## 12. No authentication and no rate limiting on the public demo

The public URL accepts a 200 MB video upload from anyone, runs CPU-bound work on it, stores
the result for 24 hours, and returns it to anyone holding the job id. There is:

- **no authentication** — no key, no session, no login;
- **no rate limiting** — nothing above the natural serialisation of a one-job pool;
- **no quota** per client, per IP, or per day;
- **no content inspection** of uploads beyond "does one frame decode?";
- **no retention policy** beyond the 24 h TTL, and no way for a user to delete their own
  upload earlier.

The mitigations that do exist are structural rather than deliberate security controls: the
single-job pool means an attacker gets a queue rather than a fork bomb, `MAX_UPLOAD_MB` and
`MAX_VIDEO_DURATION_S` bound one request, `SLAM_MAX_FRAMES` bounds one job, uploads are
streamed to disk rather than buffered in memory, and both containers run as uid 10001 with the
sample mount read-only.

**Job ids are unguessable** (`uuid.uuid4().hex`, 122 random bits) so they are capability tokens in
practice, but that is security through entropy, not authorisation — anyone who obtains the id
has full access to the artefacts.

Threat model and what a production version would need:
[08 — Security & cost](08-security-and-cost.md).

---

## 13. Smaller things, named anyway

- **`/preview/{frame_index}` re-detects ORB** on the requested frame rather than reprojecting
  map points, because the contracted `Reconstruction` carries neither per-frame keypoints nor
  intrinsics. It shows what the *detector* saw, not what the *tracker* matched. The
  tracked-point count and reprojection error in the annotation are real; the drawn points are
  not the tracked set.
- **Median track length is 2.** A map point is typically seen by only two keyframes — the
  minimum the culler allows. A longer-track map would be better conditioned; getting there
  means better descriptor matching across viewpoint change, which is item 1 wearing a
  different hat.
- **Poses are emitted only for successfully tracked frames.** A 300-frame clip can return 296
  or 144 poses. Nothing is interpolated or back-filled, which is the honest choice, but a
  consumer that assumes one pose per frame will be wrong.
- **The API cannot be given a calibration.** `SlamConfig` accepts `fx/fy/cx/cy` and honours
  them exactly; `POST /jobs` does not expose them. A reviewer with a calibrated camera cannot
  use it.
- **`enable_loop_closure` is the only algorithm knob the API exposes.** Everything else —
  including `kf_min_parallax_ratio`, the one knob this document spends its first section on —
  is server-side configuration.
- **Run history is per browser tab.** The loop-closure A/B comparison keeps its state in
  `sessionStorage`, because the contract's `Job` deliberately does not echo back the options a
  run was started with.
- **No WebGPU path**, WebGL2 only. At 1.65 ms/frame there is no reason to add one, but a
  40× larger cloud would need one.
- **The synthetic clips are synthetic.** Two of the three benchmark clips were rendered by
  `samples/generate_synthetic.py`. Procedural textures have unnaturally uniform corner
  density, no photometric noise beyond what the H.264 encode introduces, no auto-exposure and
  no motion blur. They make the drift claim *checkable* against exact ground truth, which is
  why they exist, but a system tuned on them will flatter itself. That is part of why the real
  TUM clip is the one that exposes item 1.

---

## What two more weeks would buy, in priority order

1. **An adaptive keyframe policy** driven by predicted inter-frame rotation, so item 1 is
   fixed without paying item 1's latency on every clip.
2. **Pin the benchmark to the shipped stack and record library versions** in `results.json`
   (item 8). Half a day, and it is the difference between a reproducible claim and a
   plausible one.
3. **Undistort when coefficients are known, and expose calibration through the API** (items 5
   and 6).
4. **Multi-view triangulation with uncertainty propagation** to attack the depth bias from the
   side that actually works (item 2).
5. **Persist the job table** — SQLite would do — so a restart does not orphan artefacts
   (item 11).
6. **A rate limiter and a per-IP quota** on the public demo (item 12).
