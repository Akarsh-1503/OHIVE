# 04 — API reference

Base URL `/api/v1`. All JSON is `snake_case`; all timestamps are ISO-8601 UTC with a `Z`
suffix. The frozen contract is [`_contracts/assignment1-api.md`](../../_contracts/assignment1-api.md)
and the implementation is asserted against it by the test suite — every response shape in
`tests/test_contract.py` is checked field by field.

Interactive OpenAPI docs are served at **`/docs`** on any running instance
(<http://localhost:8000/docs> locally).

Every example below was copied from a live run of the stub-mode stack. Replace the host with
`https://leads-34-47-153-95.nip.io` to run them against the deployment.

```bash
API=http://localhost:8000/api/v1
```

## Endpoints at a glance

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/batches` | Upload cards. Returns a `Batch` with everything `queued` and starts processing. |
| `GET` | `/batches/{batch_id}` | Full `Batch` snapshot. The polling fallback. |
| `GET` | `/batches/{batch_id}/events` | **SSE** live progress. |
| `POST` | `/batches/{batch_id}/retry` | Re-queue failed cards, or an explicit `card_ids` list. |
| `GET` | `/batches/{batch_id}/export.xlsx` | Styled three-sheet workbook. |
| `GET` | `/batches/{batch_id}/export.csv` | CSV fallback, same query parameters. |
| `PATCH` | `/leads/{card_id}` | Human correction. Pins edited fields to confidence 1.0. |
| `GET` | `/cards/{card_id}/image` | The original upload. `?thumb=1` for a 480 px WebP. |
| `GET` | `/health` | Liveness plus VLM provider, warmth and last latency. |
| `POST` | `/vlm/warmup` | Fire-and-forget wake of a scaled-to-zero GPU. |

`GET /health` is also served unprefixed, so the container healthcheck does not need to know
the API prefix.

---

## `POST /batches`

Multipart, repeatable `files`. Max 25 files per batch, 12 MB per file. Accepts
`image/jpeg`, `image/png`, `image/webp`, `image/heic`, `image/heif` and `application/pdf`
(first page rendered).

```bash
curl -sS -X POST "$API/batches" \
  -F "files=@samples/cards/card_01_left_align_clean.png" \
  -F "files=@samples/cards/card_02_centered_serif_clean.png" \
  -F "files=@samples/cards/card_14_dup_of_02_blur.jpg" \
  -F "files=@samples/cards/card_08_no_email_clean.png"
```

`201 Created`, returning immediately with every lead `queued` — the work is already running in
the background:

```jsonc
{
  "batch_id": "0cd00d15f2d648bab0d84f2aedfa7957",
  "status": "queued",
  "total": 4, "completed": 0, "failed": 0, "pending": 4,
  "created_at": "2026-09-15T04:31:53Z", "finished_at": null, "elapsed_ms": 0,
  "leads": [ /* four Lead objects, status "queued" */ ]
}
```

Validation happens before the body is read where possible: `Content-Length` above the batch
ceiling is rejected with `413` by middleware, and an unsupported type is rejected per file.

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$API/batches" -F "files=@Makefile"
# 422
```

```json
{
  "detail": "Makefile: application/octet-stream is not accepted. Upload JPEG, PNG, WebP, HEIC/HEIF or PDF.",
  "code": "UNSUPPORTED_MEDIA_TYPE"
}
```

Browsers mislabel HEIC as `application/octet-stream`, so the file extension is accepted as a
second opinion — but one of the two must match.

---

## `GET /batches/{batch_id}`

```bash
curl -sS "$API/batches/$BATCH"
```

`Batch.completed` counts every card the model finished reading — lead status `completed`
**and** `needs_review`, i.e. "extracted". `total == completed + failed + pending` always
holds. There is no separate `needs_review` counter; derive the split by counting `leads[]`,
which is what the review-queue stat in the UI does.

One lead, complete and unedited:

```jsonc
{
  "card_id": "d7e892f1fd8d4edea5e1c5e0ad727f4b",
  "batch_id": "0cd00d15f2d648bab0d84f2aedfa7957",
  "filename": "card_01_left_align_clean.png",
  "status": "completed",

  "first_name": "Elena",
  "last_name": "Vasquez",
  "job_title": "Founder & CEO",
  "company": "Ironvale Manufacturing",
  "location": "Singapore, Singapore",
  "phone": "+65 6812 9947",          // exactly as printed
  "phone_e164": "+6568129947",       // null when unparseable
  "email": "elena.vasquez@ironvale.co.uk",
  "website": "ironvale.co.uk",       // bare registrable host

  "confidence": {
    "first_name": 0.99, "last_name": 0.98, "job_title": 0.93, "company": 0.99,
    "location": 0.97, "phone": 0.91, "email": 0.99, "website": 0.98
  },
  "overall_confidence": 0.9728,
  "quality_flags": [],
  "duplicate_of": null,
  "raw_text": "Elena Vasquez\nFounder & CEO\nIronvale Manufacturing\n…",
  "edited": false,
  "processing_ms": 136,
  "error": null,
  "created_at": "2026-09-15T04:31:53Z"
}
```

Field notes that matter to a consumer:

- **Every extracted field is nullable.** A card that prints no email returns `"email": null`
  with `confidence.email == 0.0`, not an empty string and not an invented address.
- **`processing_ms` is work, not wall clock** — preprocessing plus inference, measured inside
  each worker. A card that queued behind five others is not reported as a slow card. Batch
  wall clock is `Batch.elapsed_ms`.
- **`raw_text` is `null` until the model has read the card**, i.e. for `queued` and `failed`
  leads. Reporting `""` there would read as "the card was blank".
- **`quality_flags` is reviewer-facing.** Informational agreement flags that only explain a
  confidence bump are deliberately not included.

---

## `GET /batches/{batch_id}/events` — SSE

`Content-Type: text/event-stream`. Each message is `event: <name>` followed by `data: <json>`.

```bash
curl -sSN "$API/batches/$BATCH/events"
```

```
event: batch.snapshot
data: {"batch_id":"0cd00d15…","status":"processing","total":4,"completed":0,…}

event: card.started
data: {"card_id":"d7e892f1…","filename":"card_01_left_align_clean.png","started_at":"2026-09-15T04:31:53.411Z"}

event: card.completed
data: {"card_id":"d7e892f1…","status":"completed","first_name":"Elena",…}

event: batch.progress
data: {"completed":1,"failed":0,"total":4,"elapsed_ms":142}

event: batch.completed
data: {"batch_id":"0cd00d15…","status":"completed",…}
```

| Event | Payload |
|---|---|
| `batch.snapshot` | Full `Batch`. Sent once, on connect. |
| `card.started` | `{card_id, filename, started_at}` — millisecond precision, **server-stated** so a client joining mid-batch can still time the card. |
| `card.completed` | The full `Lead`. |
| `card.failed` | `{card_id, error}` |
| `batch.progress` | `{completed, failed, total, elapsed_ms}` |
| `batch.completed` | Full `Batch`. The stream closes after this. |
| `ping` | `{}` every 15 s, keep-alive. |

Three guarantees the implementation provides, all covered by tests:

- **No gap on connect.** The subscriber's queue is registered *before* the snapshot is built,
  so no event can slip between the two.
- **A late subscriber still terminates.** Connecting after the batch has finished yields the
  snapshot plus a `batch.completed` synthesised from the database — which also covers the
  case where the server restarted between the upload and the subscription.
- **Progress is coalesced** to at most one event per 200 ms, with the final one always sent.
  At 6-way concurrency the uncoalesced stream is a burst of writes no UI can use.

Why SSE rather than WebSockets or polling:
[ADR-0005](adr/ADR-0005-sse-over-websockets-and-polling.md).

---

## `PATCH /leads/{card_id}`

Body is a partial field map. Sets `edited: true`, pins each edited field's confidence to 1.0
and re-scores the record.

```bash
curl -sS -X PATCH "$API/leads/$CARD_ID" \
  -H 'content-type: application/json' \
  -d '{"first_name":"Marta","last_name":"Kowalczyk-Reyes"}'
```

```jsonc
{
  "first_name": "Marta",
  "last_name": "Kowalczyk-Reyes",
  "overall_confidence": 0.5506,   // was 0.41
  "status": "needs_review",       // still queued: the rest of the card is a blurred read
  "edited": true,
  "quality_flags": ["blurry"]
}
```

A patched `phone` is re-normalised against the region implied by the (possibly also patched)
location; `website` is reduced to a bare host; `email` is lower-cased. An empty string clears
a field to `null`. An empty body is `422 EMPTY_PATCH`.

`status` may be set explicitly — that is how the UI marks a reviewed card as accepted without
touching its fields.

---

## `POST /batches/{batch_id}/retry`

```bash
curl -sS -X POST "$API/batches/$BATCH/retry" \
  -H 'content-type: application/json' -d '{"card_ids":["…"]}'
```

Omit the body to re-queue every `failed` card. Returns the updated `Batch` and restarts
streaming, so an open SSE connection picks the work back up. The duplicate window is seeded
from the cards that already succeeded, so retrying does not lose relationships established on
the first pass. A `card_id` that is not in this batch is `422 CARD_NOT_IN_BATCH`.

---

## Exports

```bash
curl -sS -OJ "$API/batches/$BATCH/export.xlsx"
curl -sS -OJ "$API/batches/$BATCH/export.csv?include_low_confidence=false"
```

| Query parameter | Default | Effect |
|---|---|---|
| `include_low_confidence` | `true` | `false` omits leads below `CONFIDENCE_THRESHOLD`. |
| `include_duplicates` | `true` | `false` omits rows with a non-null `duplicate_of` from the leads sheet — they are still counted on the summary sheet. |

Filename: `leadforge-<batch-short-id>-<yyyymmdd>.xlsx`. Three sheets:

- **Leads** — a branded title band carrying the batch id, source-file count, generated-at
  timestamp and the model that produced the data; frozen and auto-filtered header;
  auto-fitted columns; a red→amber→green colour scale on the confidence column; duplicate
  rows tinted amber, `needs_review` yellow, `failed` red; clickable `mailto:` / `tel:` /
  `https:` links; `phone_e164` forced to text so Excel keeps the leading `+`; landscape print
  area. Columns: `# · Status · First Name · Last Name · Job Title · Company · Location ·
  Phone · Phone (E.164) · Email · Website · Confidence · Quality Flags · Duplicate Of ·
  Source File · Processing (ms)`.
- **Summary** — totals, completed / needs-review / failed / duplicate counts, mean confidence,
  batch and per-card timings, per-field fill rate with its own colour scale, and the provider
  and model used.
- **Raw** — the model's transcription and raw JSON per card, so any value on sheet one can be
  audited against what the model actually saw.

The CSV carries the same rows plus every per-field confidence as its own column, which is the
shape `samples/evaluate.py` scores against ground truth.

---

## `GET /cards/{card_id}/image`

```bash
curl -sS -o card.jpg "$API/cards/$CARD_ID/image"
curl -sS -o thumb.webp "$API/cards/$CARD_ID/image?thumb=1"
```

Returns the original upload, cached for a day. `?thumb=1` returns a 480 px WebP, rendered once
and then cached on disk. HEIC and PDF uploads — which a browser cannot put in an `<img>` — are
transcoded to JPEG on first request and cached.

---

## `GET /health`

Deliberately **does not probe the model.** Any request wakes the scaled-to-zero GPU, so a
polling frontend would pin a container warm and burn GPU-seconds. `warm` is derived from the
time since the last successful completion against the 900 s scale-down window, and
`endpoint_reachable` is observed from real traffic rather than tested.

```bash
curl -sS "$API/health"
```

```json
{
  "status": "ok",
  "version": "1.0.0",
  "vlm": {
    "provider": "stub",
    "model": "Qwen/Qwen2.5-VL-3B-Instruct",
    "endpoint_reachable": true,
    "warm": true,
    "last_latency_ms": null
  },
  "uptime_s": 11
}
```

`status` is `degraded` when the endpoint has been observed unreachable. `provider` is one of
`modal`, `openai_compatible`, `stub`.

## `POST /vlm/warmup`

```bash
curl -sS -X POST "$API/vlm/warmup"
# {"warming": true}
```

Returns immediately and wakes the endpoint in the background with a one-token completion
(cheap: it generates nothing worth generating). Concurrent calls collapse into one. This is
what the UI fires when it sees a cold model and the user has cards queued, so the ~210 s boot
overlaps with them finishing their drag-and-drop instead of following it.

---

## Errors

Every non-2xx response is `{"detail": "<human readable>", "code": "<SNAKE_CASE_CODE>"}`.

| Status | Code | When |
|---|---|---|
| 404 | `BATCH_NOT_FOUND`, `CARD_NOT_FOUND`, `IMAGE_NOT_FOUND` | Unknown id |
| 413 | `PAYLOAD_TOO_LARGE` | Body above the whole-batch ceiling; rejected before it is read |
| 422 | `NO_FILES`, `TOO_MANY_FILES`, `UNSUPPORTED_MEDIA_TYPE`, `FILE_TOO_LARGE`, `EMPTY_FILE`, `EMPTY_PATCH`, `CARD_NOT_IN_BATCH`, `VALIDATION_ERROR` | Rejected upload or patch |
| 500 | `INTERNAL_ERROR` | Unhandled; the exception class is named, never the stack trace |

Every response carries an `X-Request-ID` header — echoed from the request if supplied, else
generated — and the same id appears in the structured JSON log line for that request.
