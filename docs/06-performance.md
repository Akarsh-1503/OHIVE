# 06 — Performance

Every number here was measured. Nothing is extrapolated unless it says so. The methodology is
written to be re-runnable rather than merely quotable.

**The headline, stated before the flattering numbers:** on a scale-to-zero GPU, **cold start
dominates the first request completely.** A 25-card batch takes 37–50 s warm and about 260 s
from cold, of which ~210 s is the GPU booting. That is the deliberate cost of an endpoint that
bills nothing at idle, and no amount of pipeline optimisation touches it.

## Test environments

Three distinct environments produce the three families of numbers below. Conflating them would
be the easiest way to publish a misleading figure.

| | **A — model serving** | **B — pipeline** | **C — deployment host** |
|---|---|---|---|
| What it measures | GPU inference latency and throughput | Everything *except* inference | Where the public demo runs |
| Compute | 1 × NVIDIA **L4** (24 GB), serverless container | Apple M-series laptop, 10 cores | GCE `c3-standard-8` |
| CPU | ~1 vCPU, 2.7 GiB in the container | — | Intel Xeon Platinum 8481C @ 2.70 GHz, 8 vCPU / 4 physical cores |
| Memory | — | — | 31 GiB usable (32 GiB provisioned) |
| OS | Modal container image | macOS | Ubuntu 24.04 LTS |
| Region | US | — | `asia-south1-b` |
| Client | laptop in `ap-south` — **public-internet RTT included** | in-process `TestClient` | — |
| Engine | vLLM 0.28.0, V1, `--enforce-eager`, xgrammar | `VLM_PROVIDER=stub` | Docker + Caddy |

**Caveat that shapes every GPU figure: Modal L4 containers vary by roughly 25–30 % between
instances.** Every serving number below is therefore a *range over multiple runs on different
containers*, never a single best sample. The ranges are wide because the hardware is, not
because the measurement is sloppy.

**Second caveat, stated plainly:** the serving figures were measured while building the
endpoint, in a separate Modal workspace. Standing the identical deployment up in the current
workspace re-verified *correctness* but did not re-run the full benchmark suite — so treat
them as characteristic of this configuration on an L4 rather than as fresh measurements of
this specific deployment.

---

## A — Model serving

### Cold start

**Method.** Scale the app to zero (wait past the `scaledown_window` — 240 s when these numbers
were measured, 900 s in the shipped deployment — or redeploy), then
time a single client-observed request end to end. Repeated 8 times across three
configurations; the client follows the `303` the platform returns while a container boots, so
boot time is included rather than hidden.

```bash
cd infra/modal
VLM_BASE_URL=<endpoint>/v1 VLM_API_KEY=… python smoke_test.py   # prints parsed JSON + latency
```

| Configuration | Range | Median | Boots sampled |
|---|---|---|---|
| First-ever boot, empty volumes, torch.compile cold | 258–300 s | — | 1 |
| `--no-enforce-eager`, caches warm | 240–248 s | 246 s | 3 |
| **`--enforce-eager`, caches warm (shipped)** | **187–237 s** | **210 s** | 5 |

Where a 210 s boot goes, from the container's own logs:

| Phase | Time |
|---|---|
| Modal container start, image pull, Python import | ~50 s |
| Weight load from the cache Volume | 4 s |
| vLLM engine init — dominated by dummy forward passes in `compile_or_warm_up_model`, **not** by compilation | ~105 s |
| Multimodal warmup | 13 s |
| uvicorn binds and answers | remainder |

That profile is why moving Triton and Inductor caches into a persistent volume did not
measurably help: the time is spent running the model, not compiling it. Reported as a negative
result rather than quietly dropped.

### Warm inference

**Method.** Three fixed fixtures (`samples/card_0{1,2,3}.png`, 1050×600; `card_03` is rotated,
blurred and unevenly lit to stand in for a phone photo), base64-encoded into a
`data:image/jpeg` content part, with the same JSON schema the backend uses and
`temperature=0`. Fixed inputs on purpose, so timings stay comparable across runs — extraction
*quality* is scored separately, against a different corpus.

| | |
|---|---|
| Per card, one at a time | **6.2–11.4 s** (6.6 s mean on the fastest container sampled; 9.8–10.6 s typical) |
| Output length | ~199 tokens/card — the schema includes a full `raw_text` transcription |
| Single-stream decode | 18–31 tok/s |

### Warm, 6 concurrent

**Method.** Six cards issued in parallel, matching `VLM_CONCURRENCY=6`, measured as wall time
for the pass.

| | `--enforce-eager` (shipped) | `--no-enforce-eager` |
|---|---|---|
| Wall time for 6 cards | 9.0–11.9 s | 7.5–7.7 s |
| Throughput | **0.50–0.67 img/s**, 100–133 output tok/s | 0.78 img/s, 157 output tok/s |

So a full 25-card batch is **37–50 s warm**. The eager-vs-graphs gap is real, but part of the
spread is instance variance: only one non-eager container was sampled, which is why the
comparison is presented as directional rather than precise.

### What a user actually waits for

| Scenario | Wall time |
|---|---|
| 25 cards, endpoint already warm | **37–50 s** |
| 25 cards, endpoint cold | **~250–290 s** — ~210 s of which is the GPU booting |
| First card visible in the UI, warm | ~7–12 s after upload |
| First card visible in the UI, cold | after the boot completes |

Two mitigations, neither of which pretends the cold start is not there: `POST /vlm/warmup`
overlaps the boot with the user's drag-and-drop, and the UI shows a *Cold* pill with an
explicit warning instead of a spinner that looks hung.

---

## B — The rest of the pipeline

**Method.** `make bench` posts 25 synthetically rendered cards through the real HTTP path with
`VLM_PROVIDER=stub`, so the measurement isolates *our* overhead — upload, decode, quality
metrics, 1280 px resample, JPEG re-encode, normalisation, cross-validation, dedup, persist,
event publishing — from model inference. It then exports the workbook and times that too.

```bash
make bench            # 25 cards by default
CARDS=100 make bench  # any batch size
```

Three consecutive runs, environment B, on 2026-09-15:

| | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| 25 cards, upload → `completed` | 0.61 s (40.9 cards/s) | 0.60 s (41.8 cards/s) | 0.62 s (40.1 cards/s) |
| Mean per-card pipeline work | 119 ms | 118 ms | 118 ms |
| `.xlsx` export, 25 rows, 3 sheets | 23 ms | 22 ms | 22 ms |

Earlier recorded runs on larger source images span 195–275 ms per card, and ~230 ms for a
12 MP phone photo against ~100 ms for a 1.2 MP card — per-card cost scales with the *input*
resolution, because decode and resample dominate it. The bench fixture is a 1400×850 card,
which is why it sits at the fast end.

The takeaway is the ratio, not the absolute: **our own processing is ~0.1–0.3 s per card
against 6–11 s of inference.** Everything outside the model is noise at this scale, which is
exactly why the optimisation effort went into the serving configuration and the cold start
rather than into the pipeline.

Preprocessing runs on its own thread pool, sized to the cores actually available to the
process (`sched_getaffinity` inside a container, `cpu_count` elsewhere) and capped at 8, so it
overlaps with inference rather than queueing behind it. Handing 25 photos to the default
executor at once just makes 25 threads fight over the GIL and every card look slow.

---

## Accuracy

**Method.** Run a batch through the **live** model, export it, and score it field by field
against ground truth generated from the same records that rendered the cards — so images and
expectations cannot drift apart.

```bash
# with VLM_PROVIDER=modal — upload samples/cards/ (20 files fits one batch), then:
curl -s "$API/batches/$BATCH/export.csv" -o batch.csv
python samples/evaluate.py batch.csv --errors --json result.json
python samples/evaluate.py --self-test     # proves the harness itself
```

Scoring a `stub`-provider batch correctly reports ~0 % — the stub invents people rather than
reading the cards, so the scorer is not fooled by it. That is a useful sanity check that the
harness is actually comparing pixels-derived output against ground truth.

The scorer normalises before comparing: NFKC, case-folded, whitespace-collapsed, surrounding
punctuation stripped; email case-folded; phone reduced to E.164 digits *tolerating a missing
country code*; website stripped of scheme, `www.` and trailing slash. Per field per card the
outcome is `tp` / `tn` / `fn` / `fp` / `wrong`, and `wrong` counts as both a false positive and
a false negative — so a confidently incorrect value is penalised twice, which is the correct
treatment for this product.

Five cards were run through the live model — four non-degraded (`clean` / `edge`, one of them
a `no_email` card) and one deliberately degraded duplicate — for forty scored fields:

| | Non-degraded cards | Including the degraded card |
|---|---|---|
| Fields correct | **28/32 (88 %)** | 30/40 (75 %) |

The corpus holds 20 cards with exact ground truth; five of them were scored against the live
GPU. **That is a thin sample and the percentages carry correspondingly wide error bars** —
one card is 2.5 % of the "all five" figure. Scoring the full 20 is a one-command job
(upload the directory, export the CSV, run the scorer) and is the first thing to do with more
GPU budget. It is [limitation #5](07-limitations.md).

**Name, company, phone, email and website were correct on every non-degraded card.** The
residual mismatches are all `location`, and they differ from ground truth *only by a retained
postcode* — `Boston, MA 02220, USA` where the truth says `Boston, MA, USA`. That was left
alone deliberately: a postcode is not wrong, it is more than was asked for, and tuning the
prompt until the metric goes green would be over-fitting a 20-card corpus rather than
improving the product.

Two real defects were found by this comparison and fixed, which is the argument for having it:

1. **Location was wrong on 4 of 5 cards** because the prompt asked for "the fullest address
   line available" and the model faithfully obliged with street, suite and postcode. The
   prompt now names the parts to omit. Re-measured live: the street line is gone.
2. **A URL misread the email got right.** One card prints its URL in caps; the model returned
   `HELIIXMERIDIAN.EXAMPLE` while reading `m.kowalczyk-reyes@helixmeridian.example` correctly.
   `repair_website_from_email` now corrects the host from the validated email domain when the
   two differ by at most two characters, and floors that field's confidence so the repair is
   visible. Confirmed working on the live run.

On the separate three-card GPU benchmark fixtures, **3/3 cards extracted with every field
correct on every run**, including the blurred and rotated one.

### The number that is not in the table

The degraded duplicate produced substantially hallucinated fields — wrong company, wrong
phone, wrong domain — and scored **0.88 confidence**. It was correctly flagged `blurry`, but
0.88 is far too high, and because its extracted fields diverged so far from the source card it
was *not* matched as a duplicate. That is the honest limit of a 3B VLM on a heavily blurred
image, and the honest limit of the confidence model. It is [limitation #3](07-limitations.md).

---

## Cost per unit of work

| | |
|---|---|
| Per card, warm | ~1.9 GPU-container-seconds ≈ **$0.0005** |
| 25-card batch, warm | ~$0.011 |
| 25-card batch, from cold | ~$0.28 (210 s boot + 47 s inference + 900 s idle tail) |
| Idle | **$0.00** |
| Application host | $0.4408/hr = **$10.58/day**, independent of traffic |

Full breakdown: [08 — security and cost](08-security-and-cost.md).

## Reproducing all of it

```bash
# B — pipeline throughput, offline, no GPU
make bench

# A — model serving, needs a deployed endpoint and its key
cd infra/modal && VLM_BASE_URL=<endpoint>/v1 VLM_API_KEY=… python smoke_test.py

# Accuracy, against a real batch
python samples/evaluate.py batch.csv --errors

# Correctness
make test     # 220 tests, offline
make lint     # ruff + eslint, both clean
make e2e      # Playwright smoke
```
