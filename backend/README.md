# LeadForge — backend

Business cards in. Pipeline-ready leads out.

**Live:** https://leads-34-47-153-95.nip.io — API under `/api/v1`.

Upload up to 25 business-card photos or PDFs, and a Qwen2.5-VL vision-language model
transcribes each one into a structured lead with per-field confidence. Progress streams back
over SSE, low-confidence cards land in a review queue instead of silently shipping bad data,
duplicates are flagged across the batch, and the result exports as a styled Excel workbook.

Implements `_contracts/assignment1-api.md` v1 exactly. Base URL `/api/v1`.

## Quickstart

```bash
make install       # venv + dependencies
make test          # 220 tests, fully offline (stub VLM provider)
make dev           # http://localhost:8000/docs
```

Or in Docker:

```bash
make build && make run       # http://localhost:8000/health
```

With no configuration the service runs on the `stub` VLM provider: a deterministic fake
driven by the upload's filename. No GPU, no network, no API key — the whole product is
demoable and the whole test suite passes offline. Point `VLM_PROVIDER=modal` and
`VLM_BASE_URL` at the real endpoint to switch to the model.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/batches` | multipart `files`; returns a `Batch` with everything `queued` and starts processing |
| `GET` | `/api/v1/batches/{id}` | full `Batch` snapshot (polling fallback) |
| `GET` | `/api/v1/batches/{id}/events` | SSE: `batch.snapshot`, `card.started`, `card.completed`, `card.failed`, `batch.progress`, `batch.completed`, `ping` |
| `PATCH` | `/api/v1/leads/{card_id}` | human correction; pins edited fields to confidence 1.0 |
| `POST` | `/api/v1/batches/{id}/retry` | re-queue failed cards (or an explicit `card_ids` list) |
| `GET` | `/api/v1/batches/{id}/export.xlsx` | styled workbook, `?include_low_confidence=true\|false` |
| `GET` | `/api/v1/batches/{id}/export.csv` | CSV fallback |
| `GET` | `/api/v1/cards/{card_id}/image` | stored upload, `?thumb=1` for a 480 px WebP |
| `GET` | `/api/v1/health` | liveness plus VLM provider, warmth and last latency |
| `POST` | `/api/v1/vlm/warmup` | fire-and-forget cold-start warm |

`GET /health` is also served unprefixed for the container healthcheck.

## Environment

Copy `.env.example`. Every value has a working default except `VLM_BASE_URL`, which is
required when `VLM_PROVIDER` is not `stub`.

| Variable | Default | Notes |
|---|---|---|
| `VLM_PROVIDER` | `stub` | `modal` \| `openai_compatible` \| `stub` |
| `VLM_BASE_URL` | — | OpenAI-compatible `/v1` base URL |
| `VLM_MODEL` | `Qwen/Qwen2.5-VL-3B-Instruct` | |
| `VLM_API_KEY` | — | bearer token |
| `MODAL_PROXY_KEY` / `MODAL_PROXY_SECRET` | — | sent as `Modal-Key` / `Modal-Secret` |
| `VLM_CONCURRENCY` | `6` | `asyncio.Semaphore` bound on in-flight requests |
| `VLM_TIMEOUT_S` | `420` | per-request timeout. Must be ≥ 300: a cold boot is 200–300 s and the compose default overrides the app default, so both say 420 |
| `VLM_COLD_TIMEOUT_S` | `420` | used until the endpoint is first observed warm |
| `VLM_MAX_RETRIES` | `3` | transport retries on 5xx / 429 / timeout |
| `VLM_RETRY_BASE_S` | `1.5` | backoff base, full jitter |
| `VLM_TEMPERATURE` / `VLM_MAX_TOKENS` | `0.1` / `1200` | |
| `DATA_DIR` | `/data` | SQLite database and stored card images |
| `CORS_ORIGINS` | `*` | comma-separated |
| `LOG_LEVEL` | `INFO` | structured JSON to stdout |
| `CONFIDENCE_THRESHOLD` | `0.65` | below this a lead becomes `needs_review` |
| `DUPLICATE_FUZZY_THRESHOLD` | `90` | rapidfuzz token-set ratio |
| `MAX_FILES_PER_BATCH` / `MAX_FILE_MB` | `25` / `12` | contract limits |
| `DEFAULT_PHONE_REGION` | `US` | fallback when a number has no country code and the card has no address |

## How it works

```
upload ─► preprocess ─► quality flags ─► VLM ─► normalise ─► cross-validate ─► dedup ─► store
         (EXIF, HEIC,   (focus, dark,    (JSON   (phone,      (confidence      (email,
          PDF, 1280 px,  low contrast,   schema   email,       adjustment)      phone,
          JPEG q=88)     low res)        guided)  name, URL)                    fuzzy)
```

One module per concept, no layers. The three largest concepts are packages rather than
single files; everything else is a module:

| Module | Responsibility |
|---|---|
| `app/main.py` | app factory, CORS, request-id middleware, lifespan, error shape |
| `app/routes/` | `health.py`, `batches.py`, `events.py` (SSE), `leads.py`, plus `deps.py` |
| `app/models.py` | pydantic models mirroring the contract, plus `Settings` |
| `app/store.py` | SQLite (stdlib `sqlite3`, WAL) for state, plain files for images |
| `app/vlm/` | `prompt.py` (prompt + JSON schema), `client.py` (providers, retries), `parse.py` (tolerant parse + repair turn) |
| `app/extract/` | `preprocess.py`, `normalise.py`, `confidence.py`, `dedup.py` |
| `app/pipeline.py` | batch fan-out, bounded concurrency, event bus for SSE |
| `app/export.py` | styled `.xlsx` (Leads / Summary / Raw) and `.csv` |

### What the model is asked for

A single JSON object containing a faithful `raw_text` transcription plus, for each of the
eight fields, a `{"value", "confidence"}` pair. Output is constrained with
`response_format={"type": "json_schema", ...}` so vLLM's guided decoding guarantees a
parseable response. A tolerant parser (strip code fences, find the outermost balanced
`{...}`) covers servers that ignore the parameter, and a single repair turn — handing the
model its own bad output back — recovers the rest.

### Confidence is a property of the record, not of a field

The model scores each field in isolation. This service scores the lead:

- **Quality flags are measured before the call**, on the exact downscaled image the model
  will read. Focus is the peak high-frequency response as a percentage of the card's
  ink-to-paper contrast — the textbook variance-of-Laplacian averages over the whole frame,
  and a business card is mostly blank paper, so a crisp minimalist card scores worse than a
  genuinely blurred busy one. Contrast is the extrema of a median-filtered copy for the same
  reason. Exposure and resolution round it out. The three metrics are independent, so an
  under-exposed but sharp photo is flagged `dark` and not also `blurry`.
- **Cross-field agreement raises confidence.** An email local part that matches the name
  lifts both. A domain that matches the website or the company name lifts the company. These
  are two independent readings of the same string agreeing.
- **Disagreement lowers it.** A phone number whose country does not match the address, or a
  job title identical to the company name, is discounted and flagged.
- **Validation failure floors it, never deletes.** An unparseable phone or an unrepairable
  email is kept verbatim at a capped confidence so a human sees it.
- `overall_confidence` is the weighted mean over populated fields (email and company weigh
  most, website and location least) multiplied by the quality penalties. Below
  `CONFIDENCE_THRESHOLD` the lead becomes `needs_review` — a product feature, not an error.

### OCR repair is corroborated, never speculative

`@gmall.com` is syntactically valid, so validity alone cannot decide. A repaired address is
only accepted when a second piece of the card agrees: the fixed domain matches the printed
website, matches the company name, or is a ubiquitous consumer domain. A valid address is
never overwritten by a speculative character swap. Anything left unrepaired keeps its
original text and loses confidence.

### Duplicates

Matched across the batch on normalised email, then E.164 phone, then a rapidfuzz token-set
ratio over `first + last + company` above 90. `duplicate_of` points at the first-seen card
and chains are collapsed to a single anchor. Nothing is dropped — the UI and the workbook
decide what to do.

### SSE

One in-process pub/sub per batch. A subscriber registers its queue *before* the snapshot is
built, so no event can slip through the gap; a subscriber that arrives after the batch
finished gets the snapshot plus a synthesised `batch.completed` reconstructed from the
database, which also covers a server restart. `batch.progress` is coalesced to at most one
every 200 ms with the final one always sent, `ping` keeps the connection alive every 15 s,
and queues are unregistered on disconnect.

### Cold starts

Modal scales the GPU container to zero. Until the first successful call — and again after
four idle minutes — requests use `VLM_COLD_TIMEOUT_S` rather than `VLM_TIMEOUT_S`, and
`/health` reports `vlm.warm: false` so the UI can warn before the user waits. `POST
/vlm/warmup` wakes the endpoint in the background and returns immediately.

## Excel export

`leadforge-<batch-short-id>-<yyyymmdd>.xlsx`, three sheets:

- **Leads** — branded title band with batch id, source-file count, generated-at and the model
  used; frozen and auto-filtered header; auto-fitted columns; red→amber→green colour scale on
  the confidence column; duplicate rows tinted amber, `needs_review` yellow, `failed` red;
  clickable `mailto:` / `tel:` / `https:` links; `phone_e164` forced to text so Excel keeps
  the leading `+`; landscape print area.
- **Summary** — totals, completed / needs-review / failed / duplicate counts, mean confidence,
  batch and per-card timings, per-field fill rate with its own colour scale, and the provider
  and model that produced the data.
- **Raw** — the model's transcription and raw JSON per card, so any value on sheet one can be
  audited against what the model actually saw.

## Measured performance

`make bench` posts 25 cards and waits for the batch to finish, against the `stub` provider —
so the number isolates *our* overhead from model inference. On an Apple M-series laptop,
10 cores:

| | |
|---|---|
| 25 cards, upload to `completed` | 0.6–0.8 s (32–41 cards/s) |
| mean per-card pipeline work | 195–275 ms (decode, quality metrics, 1280 px resample, JPEG, normalise, dedup, persist) |
| same work uncontended | ~100 ms for a 1.2 MP card, ~230 ms for a 12 MP card |
| `.xlsx` export, 25 rows, 3 sheets | 25–35 ms |

Measured against the **live** Qwen2.5-VL-3B deployment (5 cards, 2-way concurrency):

| | |
|---|---|
| cold start, scaled-to-zero GPU | **203.8 s** |
| warm inference, per card | **9.1–17.7 s** (median 15.0 s) |
| 20-card corpus, warm, `VLM_CONCURRENCY=2` | **154.5 s**, 20/20 succeeded |

Measured 2026-09-15 on the live deployment: serverless Modal **L4**,
`Qwen/Qwen2.5-VL-3B-Instruct` under vLLM, through Caddy and FastAPI on an 8-vCPU e2 VM — i.e. end-to-end per card, not
raw endpoint latency. `infra/modal/README.md` reports the VLM endpoint in isolation and is
the authority for that narrower number; this table is the authority for end-to-end.

The cold start is why `VLM_TIMEOUT_S` must be ≥ 300; the old 120 s default failed every
first request after an idle period. `GET /health` deliberately does **not** probe the model —
any request wakes the GPU, so a polling frontend would pin a container warm and burn credit.
`vlm.warm` is derived from time since the last successful completion against the 900 s
scale-down window (`WARM_TTL_S`, which must equal `scaledown_window` in the Modal deployment). Preprocessing runs on its own thread pool sized to the available cores,
so it overlaps with inference rather than queueing behind it.

## Testing

```bash
make test     # 220 tests
make lint     # ruff
make bench    # throughput against the stub provider
```

Everything runs offline. Coverage includes contract-shape assertions for every response,
the full batch lifecycle, SSE ordering with late and concurrent subscribers, table-driven
phone / email / name normalisation, duplicate detection, the confidence cross-validation
rules, the produced workbook reopened with openpyxl, every upload rejection path, and VLM
retry / repair behaviour against a scripted failing server.

## Deployment

`docker-compose.fragment.yml` holds the `leads-api` service for the root
`infra/docker-compose.yml`: port 8000, `DATA_DIR=/data` on the `leadforge-data` volume,
healthcheck included. The image runs as a non-root user and needs no GPU.

## Accuracy against ground truth

The **full 20-card corpus** in `samples/cards/`, uploaded as one batch to the live
deployment and scored with `samples/evaluate.py`. Numbers and method:
**[`samples/EVAL.md`](../samples/EVAL.md)**, raw scores in `samples/eval-results.json`.
Reproduce with `python evaluate.py <batch.json|export.csv|export.xlsx>`.

| field | F1 | | field | F1 |
|---|---:|---|---|---:|
| `first_name` | 100.0% | | `location` | 78.9% |
| `last_name` | 100.0% | | `phone` | 84.2% |
| `job_title` | 84.2% | | `email` | 84.2% |
| `company` | 94.7% | | `website` | 91.9% |

**micro F1 89.9%**, field accuracy 90.0%, exact-card rate (all eight fields right) 70.0%.

| difficulty | cards | field accuracy | exact-card |
|---|---:|---:|---:|
| clean | 1 | 100.0% | 100.0% |
| edge | 7 | **100.0%** | **100.0%** |
| degraded | 12 | 83.3% | 50.0% |

Every `clean` and `edge` card is perfect on all eight fields — including the
family-name-first card, the two-phone card, the no-email card and the bilingual Japanese
card. All remaining errors are on `degraded` inputs (motion blur, glare, low light, warp),
plus three `location` values that keep a district or building name.

Three defects were found by running this corpus and are now fixed:

- **Reversed name order.** `Tan Wei Ming` was split as first `Tan Wei Ming`, last `Ming`.
  The email local part (`weiming.tan@`) is an independent machine-written reading of the
  same name, so `reorder_name_from_email` uses it to re-partition — but only for names of
  three or more tokens, because at two tokens a `family.given` mailbox convention is
  indistinguishable from a family-first card. `first_name` and `last_name` are now 100%.
- **Postcodes and street lines in `location`.** Asking the model more insistently is not
  repeatable, so `strip_address_detail` removes them deterministically. 9/20 → 16/20 exact.
- **A URL misread that the email got right** (`HELIIXMERIDIAN.EXAMPLE`).
  `repair_website_from_email` corrects the host from the validated email domain when the
  two differ by at most two characters, and floors that field's confidence so the repair
  stays visible.

## Limitations

- Extraction quality is bounded by Qwen2.5-VL-3B. Dense, low-contrast or heavily stylised
  cards are where the review queue earns its place.
- `region_from_location` uses a curated country and city table rather than a full gazetteer,
  so an unusual address yields no region hint and phone parsing falls back to
  `DEFAULT_PHONE_REGION`.
- SQLite and the in-process event bus mean a single API replica. Horizontal scaling would
  need Postgres and Redis pub/sub; the module boundaries are drawn so that is a swap of
  `store.py` and `EventBus`, not a rewrite.
- Duplicate anchoring is by completion order within a batch, which under concurrency is not
  strictly upload order.
- Name order is assumed given-name-first. A card printing the family name first is split
  wrongly. The email local part usually disambiguates it (`weiming.tan@` against
  `Tan Wei Ming`) and the cross-validation step is the natural place to use that signal.
- A badly degraded card can score well above the review threshold while being substantially
  wrong: the quality flags discount confidence multiplicatively, but the model's own
  per-field scores stay high because it is confidently misreading. Weighting the flag
  penalty by how degraded the image is, rather than a flat factor per flag, would help.
