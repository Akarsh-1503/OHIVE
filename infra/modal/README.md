# LeadForge VLM — Qwen2.5-VL on Modal

A scale-to-zero, OpenAI-compatible `/v1` inference endpoint for business-card OCR and
structured extraction. vLLM serves **`Qwen/Qwen2.5-VL-3B-Instruct`** on a single **L4**;
the backend talks to it with the stock `openai` python client and forces valid JSON out
with `response_format: {"type": "json_schema", ...}`.

| | |
|---|---|
| Base URL | `https://<workspace>--ak-project-vlm-serve.modal.run` |
| `VLM_BASE_URL` | `https://<workspace>--ak-project-vlm-serve.modal.run/v1` (**`/v1` is appended**) |
| Modal workspace | *(set by whoever deploys it — the URL is derived from the active `modal profile`)* |
| Model id | `Qwen/Qwen2.5-VL-3B-Instruct` (revision `66285546`) |
| Engine | vLLM 0.28.0, V1 engine, xgrammar structured outputs |
| GPU | L4 (24 GB), `min_containers=0`, `max_containers=2`, `scaledown_window=900 s` |
| Auth | `Authorization: Bearer $VLM_API_KEY` on every route except `/health` and `/metrics` |

## Files

- `qwen_vlm.py` — the whole deployment: image, weights Volume, `download_model`, and the
  `serve` web function that runs `vllm serve` and proxies port 8000.
- `vlm_auth.py` — ASGI middleware that closes the routes vLLM's own `--api-key` leaves
  open. Loaded into vLLM via `--middleware`.
- `smoke_test.py` — base64-encodes local cards, calls the endpoint with the JSON schema the
  backend uses, prints parsed JSON + latency. This is the reference call shape for
  `backend/app/vlm.py`.

## Deploy

Nothing in `qwen_vlm.py` is workspace-specific — the deployment URL is derived from
whichever workspace the active Modal profile points at, so the same file deploys anywhere.
Confirm the target before deploying, because `modal deploy` will silently publish into the
wrong workspace otherwise:

```bash
modal profile current   # must be the workspace you intend to deploy into
```

```bash
# 1. one-time: create the bearer key the endpoint will require
python -c "import secrets; print('sk-akproj-' + secrets.token_urlsafe(32))"
modal secret create ak-project-vlm-auth VLM_API_KEY='<that value>'

# 2. one-time (per model): pull weights into the Volume on a CPU container,
#    so no GPU seconds are spent on a 7 GB download
modal run qwen_vlm.py::download_model

# 3. deploy
modal deploy qwen_vlm.py
```

Modal objects created, all prefixed `ak-project-`:

| kind | name | purpose |
|---|---|---|
| App | `ak-project-vlm` | the deployment |
| Secret | `ak-project-vlm-auth` | holds `VLM_API_KEY` |
| Volume | `ak-project-hf-cache` | HF weights at `/root/.cache/huggingface` (~7 GB) |
| Volume | `ak-project-vllm-cache` | vLLM / Triton / Inductor JIT artefacts at `/root/.cache/vllm` |

`modal deploy` only publishes the function; it does not start a GPU container. The first
HTTP request does, and the container exits 900 s after the last one.

The deployed URL is derived from whichever Modal workspace is active at `modal deploy` time (app `ak-project-vlm`). The live endpoint for the hosted demo is deliberately **not** published here: an unauthenticated request still boots the GPU container before the auth middleware can reject it, so a public URL is a standing invitation to burn GPU-seconds.
Nothing in `qwen_vlm.py` hard-codes that — the URL above is simply the one the active
profile produced — but it is the workspace the live backend's `VLM_BASE_URL` points at.

Verify:

```bash
curl -sS -H "Authorization: Bearer $VLM_API_KEY" \
  https://<workspace>--ak-project-vlm-serve.modal.run/v1/models
# {"object":"list","data":[{"id":"Qwen/Qwen2.5-VL-3B-Instruct", ... "max_model_len":4096, ...}]}

VLM_BASE_URL=https://<workspace>--ak-project-vlm-serve.modal.run/v1 \
VLM_API_KEY=... python smoke_test.py
```

## Auth

vLLM's `--api-key` middleware only guards paths under `/v1`, `/v2`, `/inference` and
`/cohere`. That leaves `/invocations` — a SageMaker-compatibility route that accepts the
same request bodies as `/v1/chat/completions` — completely unauthenticated, which on a
metered GPU is a direct way for a stranger with the URL to spend the budget. `vlm_auth.py`
adds a second middleware that guards everything except `/health` and `/metrics`, neither of
which runs the model. Verified against the live deployment:

```
/v1/models    no key -> 401      /health  no key -> 200
/tokenize     no key -> 401      /metrics no key -> 200
/invocations  no key -> 401      /invocations with key -> 200
```

The endpoint is publicly reachable by design (no Modal proxy auth), so the bearer key is
the only thing standing between the URL and the credit balance. Treat it accordingly.

## Measured performance

L4, the three fixed benchmark fixtures `../../samples/card_0{1,2,3}.png` (1050x600 PNG;
`card_03` is rotated, blurred and unevenly lit to stand in for a phone photo). These are
deliberately fixed inputs so timings stay comparable across runs — extraction *quality* is
scored separately against the 20-card corpus in `../../samples/cards/` by
`../../samples/evaluate.py`. All numbers are end-to-end from a laptop in `ap-south`, so
they include public-internet RTT.

A caveat that matters for reading these: **Modal L4 containers vary by roughly 25–30%**
between instances. Every figure below is a range over multiple cold starts on different
containers rather than a single flattering run.

**Cold start** — client-observed wall time of the first request against a scaled-to-zero
app, measured 8 times:

| configuration | range | median |
|---|---|---|
| first ever boot, empty Volumes, torch.compile cold | — | 300 s |
| `--no-enforce-eager`, caches warm (3 boots) | 240–248 s | 246 s |
| **`--enforce-eager`, caches warm (5 boots, shipped)** | **187–237 s** | **210 s** |

Where a 210 s boot goes: ~50 s Modal container start, image pull and python import; 4 s
weight load from the Volume; ~105 s vLLM engine init, which is dominated by dummy forward
passes in `compile_or_warm_up_model` rather than by compilation; 13 s multimodal warmup;
then uvicorn binds.

**Warm**, one request at a time:

| | |
|---|---|
| per card | 6.2–11.4 s (6.6 s mean on the fastest container sampled, 9.8–10.6 s typical) |
| output | ~199 tokens/card — the schema includes a full `raw_text` transcription |
| decode | 18–31 tok/s single-stream |

**Warm, 6 concurrent** (matches `VLM_CONCURRENCY=6`), 6 cards per pass:

| | `--enforce-eager` (shipped) | `--no-enforce-eager` |
|---|---|---|
| wall for 6 cards | 9.0–11.9 s | 7.5–7.7 s |
| throughput | 0.50–0.67 img/s, 100–133 output tok/s | 0.78 img/s, 157 output tok/s |

So a full 25-card batch takes **37–50 s warm**. The eager-vs-graphs gap is real but part of
the spread above is instance variance; only one non-eager container was sampled.

**Accuracy.** 3/3 cards extracted with every field correct on every run, including the
blurred and rotated one — first/last name, job title, company, location, phone, email,
website and a faithful `raw_text`. Qwen2.5-VL-**3B** is sufficient for this task; there is
no reason to go to 7B.

### Tuning notes

- **`--enforce-eager`** is the one genuine trade here: ~35 s off the median cold start for
  ~20% of decode throughput. It wins only because this endpoint scales to zero — on a cold
  25-card batch (210 s boot + 47 s inference) it beats graphs (246 s + 32 s) by ~20 s, and
  on a pre-warmed endpoint it loses. One constant in `qwen_vlm.py` flips it.
- **`--limit-mm-per-prompt '{"image": 1, "video": 0}'`** — with `video` left at its default,
  vLLM profiles and warms the engine against a *maximum-size video*. Setting it to 0 cut
  peak activation from 0.54 GiB to 0.21 GiB and freed 0.3 GiB for KV cache, for a
  capability this endpoint never exposes.
- **`--mm-processor-kwargs '{"min_pixels": 200704, "max_pixels": 1003520}'`** — caps a card
  at 1280 visual tokens (one token per 28x28 patch), so a 4096-token window comfortably
  holds the image, the grammar and the answer. Without the cap Qwen2.5-VL will spend 12 k+
  tokens on a large photo and overflow the window.
- **`TRITON_CACHE_DIR` / `TORCHINDUCTOR_CACHE_DIR` in the cache Volume.** Honest result:
  this did **not** measurably move cold start, because vLLM's warmup is dominated by dummy
  forward passes rather than compilation. Kept because it stops the artefacts landing on
  ephemeral `/tmp`, which matters if `ENFORCE_EAGER` is turned off.
- **`cpu=4.0` was tried and removed.** It bought ~6% throughput for ~16% more per
  container-second, which is the wrong trade on a cold-start-dominated workload.

## Cost model

Modal charges per container-second while a container exists and nothing while scaled to
zero. L4 is $0.000222/s; this container also draws ~1 core and ~2.7 GiB:

```
L4          $0.000222 / s
CPU+memory  $0.000019 / s
total       $0.000241 / s   =  $0.87 / hour of container wall-clock
```

| scenario | container seconds | cost |
|---|---|---|
| 25-card batch from cold | 210 boot + 47 infer + 900 idle tail | **$0.28** |
| 25-card batch, already warm | 47 infer (+ shared idle tail) | **$0.011** |
| per card, warm | ~1.9 | $0.0005 |
| idle overnight | 0 | $0.00 |

The `scaledown_window` is the main lever, and it is deliberately set to **900 s** rather than
the 240 s this was originally tuned to. Cold start is ~190–260 s and is *model-load-bound,
not compute-bound* — a bigger GPU would not meaningfully shorten it, so the only way to stop
a reviewer paying it twice is to keep the container up longer between batches. At 900 s an
abandoned tab costs ~$0.22 of idle instead of ~$0.06, which is the entire trade: roughly
$0.20 per session to remove a 3½-minute wait from a reviewer's second upload. Weight download
runs on a CPU container (28 s, ~$0.0005) rather than burning L4 seconds.

`WARM_TTL_S` in `backend/app/vlm/client.py` must be kept equal to this value — the backend
derives the `vlm.warm` health pill from time-since-last-completion, so a mismatch makes
`/health` report "cold" while the GPU is in fact still up (or vice versa).

Building and benchmarking this endpoint — 9 cold starts, the throughput passes, the
eager/graphs A/B and the image builds — cost **$0.83** of Modal credit. That work was done
in a separate workspace; standing the same deployment up here cost **$0.07** (one weight
download, one cold start, one extraction), which is what a clean redeploy should cost.

The performance figures above were measured on the original workspace. They were not
re-measured here — only correctness was re-verified — so treat them as characteristic of
this configuration on an L4 rather than as fresh measurements of this specific deployment.

## Swapping the model

Change two constants at the top of `qwen_vlm.py`, then re-run the download and deploy:

```python
MODEL_NAME = "Qwen/Qwen2.5-VL-7B-Instruct-AWQ"
MODEL_REVISION = "<commit sha from the HF model page>"
```

```bash
modal run qwen_vlm.py::download_model && modal deploy qwen_vlm.py
```

The revision is pinned deliberately: an upstream re-upload should never silently change
what a deployed endpoint serves. 7B-AWQ fits an L4 (~6 GB of weights) and leaves ~13 GB for
KV cache; expect roughly 2x the per-card latency. `VLM_MODEL` in the backend must be
updated to match, because `--served-model-name` is the full HF id.

## Backend integration

```bash
VLM_PROVIDER=modal
VLM_BASE_URL=https://<workspace>--ak-project-vlm-serve.modal.run/v1
VLM_MODEL=Qwen/Qwen2.5-VL-3B-Instruct
VLM_API_KEY=<from the ak-project-vlm-auth secret>
MODAL_PROXY_KEY=            # unused — endpoint is public, guarded by the bearer key
MODAL_PROXY_SECRET=         # unused
VLM_CONCURRENCY=6
VLM_TIMEOUT_S=420           # NOT 120 — a cold start takes 187–301 s
```

Things the backend needs to know:

- **`VLM_TIMEOUT_S` must be ≥ 300.** The contract's default of 120 s will fail every cold
  start. Either raise it, or call `POST /api/v1/vlm/warmup` (which should fire-and-forget a
  `GET /v1/models` — cheap, generates no tokens) and only then start the batch.
- **Modal answers `303` while a container boots.** Follow redirects. `openai`/`httpx` and
  `requests` do by default; `curl` needs `-L`.
- **Any request wakes the GPU**, including `/health`. Do not poll the VLM from
  `GET /api/v1/health` — derive `vlm.warm` from the time since the last successful
  completion versus the 900 s `scaledown_window`, and only probe on explicit warmup.
- **`/health` and `/metrics` are the only unauthenticated routes.** Everything else,
  including `/invocations` and `/tokenize`, returns `401` without the bearer key.
- **One image per request, no video.** Send the card as a `data:image/jpeg;base64,...`
  `image_url` content part. The backend's existing "downscale longest edge to 1280 px,
  JPEG q=88" step is exactly right — it lands just under the 1.0 MP processor cap.
- **Budget `max_completion_tokens` ≤ 1024.** `max_model_len` is 4096 and the image alone is
  ~1280 tokens.
- **Pass `temperature=0` explicitly.** The model's `generation_config.json` otherwise
  applies `repetition_penalty=1.05` and `temperature=1e-6`, which vLLM honours by default.
- **Nullable fields work.** The schema in `smoke_test.py` uses `{"type": ["string","null"]}`
  with `additionalProperties: false`; xgrammar compiles it and the model returns real
  `null`s rather than the string `"null"`.
