# 06 — Performance

> **This document answers the brief's requirement #6: "a 10-second video processed in ≤10
> seconds in the demonstrated test environment," plus "measured processing time and test
> environment."**

Two machines appear in this document and they are kept strictly apart:

| | |
|---|---|
| **Dev machine** | Apple M4, 10 vCPU, 17.2 GB, macOS, Python 3.11.9. This is where `bench/results.json` was produced and where every number labelled *dev machine* comes from |
| **Deployment host** | GCE `c3-standard-8`, Intel Xeon Platinum 8481C @ 2.70 GHz, **8 vCPU / 32 GiB** (the SLAM container is capped at 6 CPUs), Ubuntu 24.04, `asia-south1-b`. This is what the public URL runs on |

A number from one is never presented as a number from the other, and a projection is never
presented as a measurement.

---

## What `wall_ms` means

This is the number requirement #6 is about, so its scope is defined precisely, shipped
machine-readably inside every `report.json` under `timing_definition.wall_ms`, and quoted
here **verbatim** from that field:

> Span on `CLOCK_MONOTONIC` (shared by the API and worker processes) from upload-acceptance
> to reconstruction-ready, minus `queue_wait_ms`. Upload-acceptance is the instant the clip
> has been streamed to disk, proved decodable and issued a job id. Reconstruction-ready is
> the instant poses and map points exist, the full-resolution PLY and the TUM trajectory are
> written, and the web point cloud has been subsampled to `SLAM_MAX_POINTS_WEB`.
> **INCLUDED**: process-pool dispatch and argument pickling, video decode, feature tracking,
> loop-closure search, bundle adjustment and pose-graph optimisation, PLY and TUM
> serialisation, and the quality-aware point subsample. **EXCLUDED**: the client's upload
> time on the wire and the admission-time decode probe (both before the clock starts); any
> time queued behind another job (reported separately as `queue_wait_ms`, never folded in);
> and the terminal write of the reconstruction JSON plus its gzip, which cannot be inside the
> number because those bytes embed the metrics — it is measured instead as
> `payload_emit_ms`, and `accept_to_bytes_on_disk_ms` is the sum for anyone who wants it.

The last clause is the point. A metrics blob cannot contain the time taken to write itself,
so instead of quietly dropping that cost the report publishes `payload_emit_ms` alongside and
`accept_to_bytes_on_disk_ms = wall_ms + payload_emit_ms`. Measured, `payload_emit_ms` is
**18–33 ms** — under 1 % of wall — and the exclusion is auditable rather than asserted.

Two timing scopes therefore exist, and both are reported below:

| scope | what it measures | where it comes from |
|---|---|---|
| **Engine** | `SlamPipeline.run()` only — decode, track, optimise. No service, no serialisation | `bench/benchmark.py` → `bench/results.json` |
| **Service** | the contract's `wall_ms` — everything above plus pool dispatch, PLY, TUM, web subsample | `GET /jobs/{id}/export/report.json` |

---

## Methodology

Precise enough to re-run.

```bash
# What bench/results.json contains: 3 clips x 6 variants x 5 repeats.
make bench
#   == python bench/benchmark.py --repeats 5 --ablate

# One repeat, shipped defaults, all three clips:
make bench-quick

# A single clip:
python bench/benchmark.py --video samples/synthetic_loop.mp4 --repeats 5
```

| | |
|---|---|
| Clips | The three in [`../samples/`](../samples/README.md), all exactly 300 frames / 10.000 s at 30 fps |
| Repeats | 5. **Median** reported, plus `wall_s_spread` (max − min) so the variance is visible |
| Warm-up | None, deliberately. A cold first run is what a reviewer will actually experience, and it is inside the spread |
| Load | Sustained — the repeats run back to back with no cool-down, so thermal throttling is included rather than dodged |
| Configuration | `SlamConfig()` defaults, dumped verbatim into `results.json` under `config_defaults` (109 fields) so a run can be reproduced exactly |
| Accuracy | ATE = RMSE of the estimated camera centres after **Sim(3)** alignment (`geometry.align_trajectories`, Umeyama with scale) against the per-frame ground truth in `<stem>_gt.json` |
| Ablations | `no-loop-closure`, `no-local-BA`, `width-480`, `features-800`, `stride-2` |

**Why Sim(3) alignment and not SE(3).** The reconstruction has no metric scale, so comparing
raw coordinates to metric ground truth would measure the arbitrary choice of unit baseline.
Sim(3) alignment is the standard monocular protocol (it is what `evo_ape -as` does). The
recovered scale factor is reported separately as `ate_scale`, because it is exactly the
information the alignment absorbs — see
[03 — Drift mitigation](03-drift-mitigation.md#scale-drift-without-a-revisit).

**Independently checkable.** The TUM export means a reviewer can bypass our harness entirely:

```bash
curl -s "$B/jobs/$JOB/export/tum" -o traj.txt
evo_ape tum samples/synthetic_loop_gt_tum.txt traj.txt -va
```

---

## Dev machine — engine timing

Apple M4, 10 vCPU, 17.2 GB, Python 3.11.9, **median of 5, sustained**. Source:
[`../bench/results.json`](../bench/results.json), shipped defaults.

| clip | wall | ×realtime | decode | ORB | tracking | optimise | spread (max−min) |
|---|---|---|---|---|---|---|---|
| `desk_handheld_tum` | **4.32 s** | 2.32× | 103 ms | 2096 ms | 2849 ms | 1266 ms | 0.198 s |
| `synthetic_corridor` | **2.76 s** | 3.63× | 208 ms | 1699 ms | 1645 ms | 1076 ms | 0.082 s |
| `synthetic_loop` | **4.76 s** | 2.11× | 244 ms | 1884 ms | 2046 ms | 2459 ms | 0.636 s |

Reading the split correctly matters:

- **`tracking_ms` and `optimize_ms` are disjoint slices of the calling thread and sum to
  wall.** 2849 + 1266 = 4115 ms against a 4323 ms wall on the desk clip; the remainder is
  initialisation and finalisation.
- **`decode_ms` and `feature_ms` are concurrent work**, measured on their own threads and
  reported as measured. They do **not** add to wall. On the desk clip the ORB thread is busy
  2096 ms of a 4323 ms wall — about 48 % — and is almost entirely hidden behind tracking.
  OpenCV releases the GIL inside `VideoCapture.read`, `resize` and `ORB`, which is what makes
  that overlap real rather than nominal.
- **Optimisation is 25–52 % of wall.** On `synthetic_loop` it is the largest single slice
  (2459 ms), because that clip generates 39 keyframes and local BA runs after every one.

---

## Dev machine — service timing (the contract's `wall_ms`)

Same machine, through the full HTTP service with the **shipped dependency pins** (see
[the version note](#the-version-discrepancy--worth-fixing) below), `SLAM_BACKEND=real`,
median of 3, one job at a time.

| clip | `wall_ms` | ×realtime | `queue_wait_ms` | `payload_emit_ms` | keyframes | points | closures |
|---|---|---|---|---|---|---|---|
| `synthetic_loop` | **4530 ms** | 2.21× | 0 | 22 ms | 32 | 3169 | 1 |
| `synthetic_corridor` | **2831 ms** | 3.53× | 0 | 29 ms | 24 | 3951 | 0 |
| `desk_handheld_tum` | **5402 ms** | 1.85× | 0 | 18 ms | 33 | 2773 | 1 |

Every run is comfortably inside the 10 s budget on this machine, with the service overhead
(pool dispatch + PLY + TUM + subsample + emit) accounting for a few hundred milliseconds.

Reproduce:

```bash
SLAM_BACKEND=real docker compose up --build
B=http://localhost:8000/api/v1
J=$(curl -s -X POST $B/jobs/from-sample/synthetic_loop | python3 -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')
# wait for status == completed, then:
curl -s $B/jobs/$J/export/report.json | python3 -m json.tool
```

---

## Deployment host — measured

> ### Both 10-second clips land inside the 10 s budget on the public deployment. The real handheld clip runs at 13.8 s — a known, costed gap, not an open question.

Measured against the **live public URL** — `https://slam-34-47-153-95.nip.io` — on 2026-09-15,
after the `ba_max_nfev` change described below. Jobs issued strictly one at a time so nothing
contends, `SLAM_BACKEND=real`, shipped defaults. These are the contract's `wall_ms`, read back
from each job's own `report.json`.

| clip | `wall_ms` | ×realtime | poses | keyframes | closures | drift reduction | |
|---|---|---|---|---|---|---|---|
| `synthetic_corridor` | **6 583 / 6 758 ms** | 1.48–1.52× | 288/300 | 24 | 0 (no revisit) | n/a | inside budget |
| `office_handheld` (TUM fr3) | **6 788 / 7 881 ms** | 1.27–1.47× | 297/300 | 19 | 0 | n/a | inside budget |
| `synthetic_loop` | **9 588 / 9 772 ms** | 1.02–1.04× | 231/300 | 30 | 1 | **89.5 %** | inside budget |
| `desk_handheld` (TUM fr1) | **13 873 / 14 132 ms** | 0.71–0.72× | 294/300 | 49 | 2 | **99.8 %** | 39 % over |

**Three of the four bundled clips meet the 10 s budget on the deployed host, including a
real-world one.** Run-to-run spread is well under 1 %, so these are stable figures rather than
noise or a cold-start artefact.

### Host sizing, and what it costs

The engine is single-thread-bound with two helper threads, so wall time tracks single-core
performance far more than core count. Measured on the same code and the same clips:

| host | corridor | office | loop | desk | passing |
|---|---|---|---|---|---|
| `c3-standard-8` (8 vCPU, container capped at 6) | 6.6 s | 6.8 s | **9.6 s** | 13.9 s | **3 of 4** |
| `c3-standard-4` (4 vCPU) | 7.8 s | 9.6 s | 11.3 s | 15.2 s | 2 of 4 |

Two alternatives were measured and rejected. `c2d-highcpu-8` (AMD EPYC 7B13) came out
**slower than four Intel cores** despite having eight, which is the clearest evidence that
this workload wants clock rather than parallelism. `c4-standard-4` — a higher-clock Emerald
Rapids part, and the most promising option — could not be tested at all, because C4 requires
Hyperdisk and the boot disk is `pd-balanced`.

One operational note worth recording, because it produced a genuinely misleading measurement:
the SLAM container carries an explicit CPU ceiling, and after a resize that ceiling does not
follow the host. An 8-vCPU host still running a 3-CPU container measured *slower* than the
4-vCPU box (desk 18.4 s against 15.2 s). The compose limits are parameterised
(`SLAM_API_CPUS`) precisely so they can be moved with the machine type; they have to be.

### What changed, and what was rejected

The original measurement missed the budget on **two** clips (`synthetic_loop` 11 853 ms,
`desk_handheld_tum` 12 453 ms). Local bundle adjustment was 40–47 % of host wall time and scales
with keyframe count, so that was the slice to attack.

**Shipped: `ba_max_nfev` 20 → 12.** Measured against ground truth on all three clips, this cuts
optimisation 26–30 % and *improves* mean reprojection error everywhere (1.417→1.271,
0.532→0.505, 1.174→0.739 px) and ATE on two of three. The trust region reaches a good step well
before iteration 20; the remaining iterations were refining below the noise floor of the
correspondences. It took `synthetic_loop` from 11 853 ms to ~9 730 ms while *keeping* its loop
closure.

Two alternatives were measured and **rejected**:

| candidate | effect | why rejected |
|---|---|---|
| `ba_every_n_keyframes` 1 → 2 | optimise −30 % | corridor ATE 0.026 → 0.377 m (14×), desk 0.204 → 0.355 m, and it destroys the loop closure on `synthetic_loop`. |
| `kf_min_frame_gap` 3 → 4 | optimise −35 %, and on `synthetic_loop` it *restores* the closure and halves ATE (0.72 → 0.39 m) | drops that clip's pose coverage from 298/300 to **229/300**. A quarter of the trajectory would have no pose. `tests/test_pipeline.py` asserts ≥ 250 and correctly failed. Buying the latency target by silently discarding frames is not a trade this project makes. |
| `max_features` 1200 → 800 | wall −19 %, and better ATE on both failing clips | corridor ATE 0.026 → 0.519 m and coverage 285 → 195. Fixes the clips that fail by breaking the one that passes. |

### A caution for anyone retuning these knobs

The pipeline is **deterministic** for a given configuration — four repeats produce byte-identical
keyframe counts, coverage, closure counts and ATE — but it is genuinely **sensitive** to
`ba_max_nfev`. Keyframe insertion and loop detection are threshold comparisons, so a small shift
in pose estimates flips discrete decisions: `ba_max_nfev=14` yields 53 keyframes on the handheld
clip where 12 and 16 yield ~31. Sweeping for a local optimum here fits the three sample clips
rather than tuning the algorithm, and the host's keyframe counts do not track the dev machine's
(local desk = 31 keyframes, host = 49). Any candidate must be verified on the host.

Reproduce it against the live URL, no access to the host required:

```bash
B=https://slam-34-47-153-95.nip.io/api/v1
J=$(curl -s -X POST $B/jobs/from-sample/synthetic_loop | jq -r .job_id)
until [ "$(curl -s $B/jobs/$J | jq -r .status)" = completed ]; do sleep 1; done
curl -s $B/jobs/$J/export/report.json | jq '.metrics | {wall_ms, realtime_factor, keyframes}'
```

Host and configuration, self-reported by the service in every `report.json`:

```json
"host":   {"cpu": "Intel(R) Xeon(R) Platinum 8481C CPU @ 2.70GHz", "vcpu": 8, "ram_gb": 33},
"config": {"target_width": 640, "max_features": 1200, "enable_loop_closure": true,
           "max_frames": 1800, "workers": 6, "backend": "real", "max_points_web": 60000}
```

### The projection was wrong, and by how much

Before this measurement existed, the projected worst case was **8.6–9.5 s**, derived from the
core-count and clock difference: the host is a Xeon 8481C presenting 8 vCPU from **4 physical
cores**, SMT siblings add no arithmetic throughput to a workload already saturating the vector
units, and single-thread throughput on this workload is roughly 1.8–2.0× lower than an M4
performance core. Applying that factor to the slowest dev-machine engine time (4.76 s) gave
8.6–9.5 s.

Measured before tuning: **11.9 s and 12.5 s**. The projection under-estimated by **25–30 %**. It
is recorded here rather than deleted, because a projection that turned out wrong is more useful
to a reader than one quietly replaced by the number it failed to predict.

**Why it was wrong.** The projection assumed the *same work* running slower. The host does
*more* work, because the reconstruction itself differs:

| clip | keyframes, dev machine | keyframes, deployment host |
|---|---|---|
| `synthetic_loop` | 39 | **43** |
| `desk_handheld_tum` | 29 | **34** |
| `synthetic_corridor` | 24 | 23 |

Local BA runs after **every** keyframe and its cost grows superlinearly in window occupancy,
so 43 keyframes instead of 39 is not a 10 % increase in `optimize_ms` — it is 5613 ms against
2459 ms, a **2.3×** increase. The keyframe-count difference has the same root cause as
[the version discrepancy](#the-version-discrepancy--worth-fixing) below: a different OpenCV
build takes different paths through ORB and RANSAC and produces a different — here, denser —
keyframe sequence.

### What the honest claim is

- **On the dev machine (Apple M4): requirement #6 is met**, with 2–3.5× margin, both engine-
  level and service-level.
- **On the public deployment: met on both 10 s synthetic clips** — `synthetic_corridor` at
  7.0–7.6 s and `synthetic_loop` at 9.7 s — **and missed on `desk_handheld_tum` at 13.8 s**,
  38 % over.

So the brief's literal test — a 10-second video in under 10 seconds — passes on the deployment
for the two clips authored as 10-second inputs, and fails on the real-world handheld clip.

That clip is the honest hard case and it is bundled deliberately. It is 640×480 TUM RGB-D
footage containing a 151-frame section of fast rotational motion blur; the tracker responds by
inserting 49 keyframes where the synthetic clips need 24–30, and local BA runs once per
keyframe. The extra keyframes are not waste — that clip produces the best drift result in the
set (2 loop closures, **99.8 %** loop-error reduction) — but they cost 6.2 s of optimisation.

"10 seconds in the demonstrated test environment" is a claim about the demonstrated environment,
and the demonstrated environment is the public URL. The submission states both the passes and
the failure rather than quoting only the clips where the claim holds.

### What would close the gap

`ba_max_nfev` 20 → 12 has been applied and is described above. What remains for
`desk_handheld_tum` is listed here with its measured accuracy cost. **None of these has been
applied.** Applying one silently to make a number go green is exactly what this document exists
to prevent.

| change | expected effect | accuracy cost (measured, dev machine) |
|---|---|---|
| `kf_min_frame_gap` 3 → 4 | optimise −35 % | loop-clip coverage 298 → **229/300**. Fails `tests/test_pipeline.py`. Rejected. |
| `ba_every_n_keyframes` 1 → 2 | optimise −30 % | corridor ATE 0.026 → **0.377 m**, desk 0.204 → 0.355 m, destroys the loop closure. Rejected. |
| `max_features` 1200 → 800 | wall −13 % to −21 % | loop ATE 0.711 → 0.609 m, but corridor 0.026 → **0.519 m** and coverage 285 → 195. Rejected. |
| `frame_stride=2` | wall −42 % to −60 % | drops to 46/300 tracked frames on the real clip. Rejected. |
| Raise the `slam-api` container's 6.00-CPU ceiling | unknown; the host also runs Assignment 1's stack and Caddy | none |
| A larger instance (`c3-standard-16`) | ~2× | none, and ~2× the $10.58/day |

Every engine-level knob that closes the remaining gap costs either trajectory coverage or
accuracy on a clip that currently passes. The honest options for `desk_handheld_tum` are a
larger instance, or genuine algorithmic work on the blur-driven keyframe explosion — a
motion-aware keyframe policy that recognises that 49 keyframes through a blurred pan are
buying less than 49 keyframes through clean motion. That is the right fix and it is not a
knob; it is described in [07-limitations](07-limitations.md).

### Ablation provenance

The rejection table above is measured on the **dev machine** with ground truth, since ATE
requires it. `bench/results.json` carries the full 3-clip × 8-variant sweep and now records
`numpy` / `opencv` / `scipy` versions alongside each run, because an earlier edition of this
file was published from NumPy 2.4.6 / OpenCV 4.14.0 while the deployed image pins 2.1.3 /
4.10.0 — the two disagree on ATE by up to 80 %, so the numbers described a configuration
nobody was running. The host figures in the table at the top of this section are
**service-level `wall_ms` measured through the public API**, which is the number requirement #6
is actually about.

To produce it:

```bash
gcloud compute ssh ak-project-web --zone=asia-south1-b --tunnel-through-iap \
  --command 'cd /opt/ak-project/src/slam && python bench/benchmark.py --repeats 5 --ablate \
             --out bench/results-host.json'
```

### Why the deployment default is not simply "more workers"

`SLAM_MAX_CONCURRENT_JOBS` stays at **1**. A second concurrent reconstruction on an 8-vCPU /
4-core host roughly halves single-job throughput, which would make the numbers above
considerably worse rather than better. Extra jobs queue and report `queue_position`.
[ADR-0006](adr/ADR-0006-process-pool-and-single-concurrent-job.md).


---

## Ablations — what each knob costs

Dev machine, median of 5, from `bench/results.json`. `default` is the shipped configuration.

### `synthetic_loop` (8.85 m, closed)

| variant | wall | ×RT | ATE | reproj | kf | points | closures |
|---|---|---|---|---|---|---|---|
| **default** | 4.76 s | 2.11× | **0.711 m** | 1.17 px | 39 | 3431 | 1 |
| `no-loop-closure` | 4.51 s | 2.23× | 0.718 m | 0.73 px | 39 | 5247 | 0 |
| `no-local-BA` | 3.82 s | 2.63× | 0.508 m | 1.86 px | 41 | 2664 | 1 |
| `width-480` | 5.62 s | 1.79× | 0.406 m | 1.82 px | 40 | 3883 | 1 |
| `features-800` | 3.76 s | 2.67× | 0.609 m | 0.73 px | 42 | 3945 | 1 |
| `stride-2` | 2.74 s | 3.67× | 0.598 m | 0.81 px | 19 | 2887 | 0 |

### `synthetic_corridor` (9.72 m, open)

| variant | wall | ×RT | ATE | reproj | kf | points | closures |
|---|---|---|---|---|---|---|---|
| **default** | 2.76 s | 3.63× | **0.026 m** | 0.53 px | 24 | 4134 | 0 |
| `no-loop-closure` | 2.81 s | 3.58× | 0.026 m | 0.53 px | 24 | 4134 | 0 |
| `no-local-BA` | 2.38 s | 4.22× | 0.061 m | 0.72 px | 24 | 4086 | 0 |
| `width-480` | 3.31 s | 3.03× | 0.383 m | 0.60 px | 24 | 3809 | 0 |
| `features-800` | 2.80 s | 3.59× | 0.519 m | 0.73 px | 18 | 2478 | 0 |
| `stride-2` | 2.28 s | 4.40× | 0.034 m | 0.52 px | 22 | 3683 | 0 |

### `desk_handheld_tum` (5.30 m, real, TUM RGB-D)

| variant | wall | ×RT | ATE | reproj | kf | points | tracked frames |
|---|---|---|---|---|---|---|---|
| **default** | 4.32 s | 2.32× | **0.204 m** | 1.42 px | 29 | 2211 | 144/300 |
| `no-loop-closure` | 4.44 s | 2.26× | 0.168 m | 0.95 px | 31 | 4064 | 145/300 |
| `no-local-BA` | 3.44 s | 2.91× | 0.277 m | 1.77 px | 30 | 1404 | 139/300 |
| `width-480` | 6.66 s | 1.51× | 0.078 m | 1.15 px | 49 | 6380 | **294/300** |
| `features-800` | 3.52 s | 2.85× | 0.114 m | 1.48 px | 30 | 2498 | 142/300 |
| `stride-2` | 1.91 s | 5.26× | 0.130 m | 0.91 px | 14 | 1961 | 46/300 |

Four things in those tables are uncomfortable and are stated rather than trimmed:

**`width-480` is *slower*, not faster, on two of three clips** — 6.66 s against 4.32 s on the
desk clip. A coarser image changes the keyframe policy: 49 keyframes instead of 29, a 2.9×
denser map, and `optimize_ms` rises from 1266 ms to 3226 ms. Bundle adjustment grows faster
than feature extraction shrinks. This is why 640 px is the default and why "just downscale
more" is not a free latency knob.

**`width-480` is also the most accurate configuration on the real clip** — ATE 0.078 m against
0.204 m, and 294/300 tracked frames against 144/300 — and it does not fit the budget. That
trade is named again in [07 — Limitations](07-limitations.md).

**`no-local-BA` gives a *lower* ATE on `synthetic_loop`** (0.508 m vs 0.711 m) while producing
a visibly worse reconstruction (1.86 px vs 1.17 px reprojection, `scale_drift_ratio` 8.48,
loop residual 58.5 before PGO). ATE-after-Sim(3)-alignment does not penalise a uniformly
wrong scale, because the alignment absorbs it. Explained in
[03 — Drift mitigation §7](03-drift-mitigation.md#7-the-negative-results).

**`stride-2` halves the work and looks great on wall clock** (1.91 s, 5.26×) but drops to
46/300 tracked frames on the real clip. Throughput bought by throwing away the frames that
keep tracking alive is not throughput.

---

## The version discrepancy — worth fixing

`bench/results.json` was produced in the repository's standalone engine virtualenv, which
resolved to **NumPy 2.4.6 / OpenCV 4.14.0 / SciPy 1.17.1**. The shipped container — and
`backend/pyproject.toml` — pin **NumPy ~2.1 / OpenCV ~4.10**, and resolve to NumPy 2.1.3 /
OpenCV 4.10.0.

Different OpenCV versions take different code paths through ORB and the RANSAC estimators, so
the two stacks produce *different reconstructions of the same clip*. Measured, same machine,
one repeat, shipped defaults:

| clip | ATE, published stack (cv2 4.14) | ATE, **shipped** stack (cv2 4.10) |
|---|---|---|
| `synthetic_loop` | 0.711 m | **0.404 m** |
| `synthetic_corridor` | 0.026 m | **0.033 m** |
| `desk_handheld_tum` | 0.204 m | **0.364 m** |

Neither stack is uniformly better — the loop clip improves, the real clip degrades — and both
sets of numbers are inside the same order of magnitude, so no headline claim changes sign. But
the published table does not describe the deployed artefact byte for byte, and that is a
reproducibility defect rather than a rounding detail.

Two fixes, both small:

1. Run `bench/benchmark.py` in an environment pinned to the container's resolved versions (or,
   better, *inside* the API image) so the benchmark measures the artefact.
2. Make `bench/benchmark.py` record `numpy.__version__`, `cv2.__version__` and
   `scipy.__version__` into `results.json` beside the existing `python` and `platform` fields.
   It currently records the interpreter version but not the libraries that actually do the
   arithmetic, which is what let the drift go unnoticed.

Until (1) is done, the correct reading of the published tables is: **measured on the dev
machine with the engine venv, and representative of the shipped stack to within roughly a
factor of two on ATE, not identical to it.**

A related observation, from the container verification: the same clip run inside Docker
Desktop's emulated arm64 Linux VM produced `scale_drift_ratio` 18.5 and
`trajectory_length_m` 341.8, against 0.9986 and 8.9 natively. That is the low-parallax
scale-inflation failure mode being triggered by a different arithmetic path, and it is filed
in [07 — Limitations](07-limitations.md#8-platform-dependent-numerics).

---

## Frontend rendering

Separate budget, separate measurement. `make -C frontend bench`, replaying the trajectory
(worst case: the demand loop is saturated and the playhead camera moves every frame).

| renderer | 50 000 points | 60 000 points (the web cap) |
|---|---|---|
| Apple M4, ANGLE Metal, **vsync on** | **85 fps**, pinned to the display refresh | 85 fps |
| Apple M4, ANGLE Metal, vsync off | **607 fps** median — **1.65 ms/frame** | 589 fps — 1.70 ms/frame |
| SwiftShader, headless CI | 8 fps | — |

1.65 ms against a 16.7 ms budget at 60 Hz is roughly **10× headroom**. The vsync-on row is the
user-facing figure; the SwiftShader row is a correctness check in the headless harness, not a
performance claim.

First load on `/j/[jobId]`: **177 kB**, with three.js code-split and fetched while the job is
still reconstructing rather than blocking first paint.

---

## Test suite

```bash
make test      # 57 SLAM engine tests + 42 backend tests + tsc --noEmit
make lint      # ruff (engine + backend) + eslint + tsc
```

Current state, verified: **57 SLAM tests passing** (23.4 s), **42 backend tests passing**
(28.7 s, offline against the stub engine), **`ruff` clean** across `slam`, `tests`, `bench`
and `backend`, **`next build` clean**.

The engine suite asserts behaviour, not coverage: Lie-group `exp`/`log` round-trips and
adjoint identities across the small-angle branch boundary, Umeyama recovering a known
similarity and **rejecting a reflection**, the analytic BA Jacobian against finite differences
to **1e-5**, BA never writing back a worse solution, pose-graph optimisation reducing injected
drift and recovering injected scale drift, a consistent graph being a fixed point, the fixed
vertex not moving, ATE on the corridor clip against real ground truth, the focal estimate
landing in the right range, a calibration override being honoured, loop closure collapsing the
loop error, `enable_loop_closure=False` being respected, and an untrackable video degrading
gracefully instead of raising.
