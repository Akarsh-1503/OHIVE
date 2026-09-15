# LeadForge — API Contract (v1)

Frozen contract. Backend implements it; frontend consumes it. Do not change field names
without updating this file first.

Base URL: `/api/v1`. All JSON is `snake_case`. All timestamps are ISO-8601 UTC.

## Product name
**LeadForge** — "Business cards in. Pipeline-ready leads out."
Never reference the hiring company or any internal company name anywhere in this repo.

## Core objects

### `Lead`
```jsonc
{
  "card_id": "b0f1...",            // uuid4 hex, stable per uploaded image
  "batch_id": "3c2a...",
  "filename": "card_01.jpg",
  "status": "queued|processing|completed|needs_review|failed",

  "first_name": "Priya",           // string | null  — all extracted fields nullable
  "last_name": "Raghavan",
  "job_title": "VP of Partnerships",
  "company": "Northwind Robotics",
  "location": "Bengaluru, KA, India",
  "phone": "+91 80 4718 2200",     // human-readable, as printed on the card
  "phone_e164": "+918047182200",   // normalised, null when unparseable
  "email": "priya@northwind.io",
  "website": "northwind.io",       // bonus field, shown in UI, exported to Excel

  "confidence": {                  // per-field 0..1, always present, null fields -> 0.0
    "first_name": 0.97, "last_name": 0.96, "job_title": 0.91, "company": 0.98,
    "location": 0.74, "phone": 0.99, "email": 0.99, "website": 0.88
  },
  "overall_confidence": 0.93,      // weighted mean over non-null required fields
  "quality_flags": ["low_resolution", "blurry", "no_email"],  // string[] , may be empty
  "duplicate_of": null,            // card_id of the first-seen duplicate, else null
  "raw_text": "…",                 // string | null — the VLM's transcription. null until the
                                   //   model has read the card, i.e. for queued/failed.
  "edited": false,                 // true once a human PATCHed it
  "processing_ms": 2140,           // int | null — null for queued/failed. Work done on the
                                   //   card (preprocess + inference), NOT wall clock: it
                                   //   excludes time spent queued behind the concurrency
                                   //   limit, so a late card is not reported as a slow one.
  "error": null,                   // string when status=failed
  "created_at": "2026-09-14T09:12:03Z"
}
```

### `Batch`
```jsonc
{
  "batch_id": "3c2a...",
  "status": "queued|processing|completed|partial|failed",
  "total": 12, "completed": 9, "failed": 1, "pending": 2,
  "created_at": "...", "finished_at": null,
  "elapsed_ms": 18422,            // batch wall clock, unlike Lead.processing_ms
  "leads": [ /* Lead[] */ ]
}
```

`completed` counts every card the model finished reading — lead status `completed` **and**
`needs_review`, i.e. "extracted". `total == completed + failed + pending` always holds.
There is no separate `needs_review` counter on `Batch`: derive the split by counting
`leads[]` by status, which is what the review-queue stat in the UI does.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/batches` | multipart `files` (repeatable). Returns `Batch` with leads in `queued`. Starts background processing immediately. |
| `GET` | `/batches/{batch_id}` | Full `Batch` snapshot (poll fallback). |
| `GET` | `/batches/{batch_id}/events` | **SSE** live progress. See events below. |
| `PATCH` | `/leads/{card_id}` | Body: partial Lead field map. Sets `edited=true`, recomputes `overall_confidence` to 1.0 for edited fields. Returns updated `Lead`. |
| `POST` | `/batches/{batch_id}/retry` | Body `{"card_ids": [...]}` (omit -> all failed). Re-queues. Returns `Batch`. |
| `GET` | `/batches/{batch_id}/export.xlsx` | Styled Excel (`.xlsx`). Query `?include_low_confidence=true\|false` (default true), `?include_duplicates=true\|false` (default true — when false, rows with a non-null `duplicate_of` are omitted from the leads sheet but still counted in the summary sheet). |
| `GET` | `/batches/{batch_id}/export.csv` | CSV fallback. Same two query params. |
| `GET` | `/cards/{card_id}/image` | Original upload (used for side-by-side review). `?thumb=1` for a 480px webp. |
| `GET` | `/health` | Liveness + VLM status. |
| `POST` | `/vlm/warmup` | Fire-and-forget cold-start warm of the VLM. Returns `{"warming": true}`. |

### SSE events (`/batches/{id}/events`)
`Content-Type: text/event-stream`. Each message: `event: <name>` + `data: <json>`.

| event | data |
|---|---|
| `batch.snapshot` | full `Batch` — sent once on connect |
| `card.started` | `{"card_id": "...", "filename": "...", "started_at": "2026-09-14T09:12:03.411Z"}` |
| `card.completed` | full `Lead` |
| `card.failed` | `{"card_id":"...", "error":"..."}` |
| `batch.progress` | `{"completed":n,"failed":n,"total":n,"elapsed_ms":n}` |
| `batch.completed` | full `Batch` |
| `ping` | `{}` every 15 s (keep-alive) |

Stream closes after `batch.completed`.

### `GET /health`
```jsonc
{
  "status": "ok",
  "version": "1.0.0",
  "vlm": {
    "provider": "modal",              // modal | openai_compatible | stub
    "model": "Qwen/Qwen2.5-VL-3B-Instruct",
    "endpoint_reachable": true,
    "warm": true,                     // false => next call pays cold start
    "last_latency_ms": 1830
  },
  "uptime_s": 4821
}
```

## Upload rules
- Accepts `image/jpeg|png|webp|heic|heif` and `application/pdf` (first page rendered).
- Max 25 files per batch, max 12 MB per file. Reject with `422` + `{"detail": "..."}`.
- Server-side: EXIF auto-orient, downscale longest edge to 1280 px, JPEG q=88 before VLM.

## Error shape
All non-2xx: `{"detail": "human readable message", "code": "SNAKE_CASE_CODE"}`.

## Env vars (backend)
```
VLM_PROVIDER=modal                # modal | openai_compatible | stub
VLM_BASE_URL=https://<modal>.modal.run/v1
VLM_MODEL=Qwen/Qwen2.5-VL-3B-Instruct
VLM_API_KEY=                      # optional bearer
MODAL_PROXY_KEY=                  # optional, sent as Modal-Key header
MODAL_PROXY_SECRET=               # optional, sent as Modal-Secret header
VLM_CONCURRENCY=6
VLM_TIMEOUT_S=420                 # MUST be >= 300; 420 is what ships. The GPU scales to zero and a cold boot
                                  #   measures 187-237 s, so the old 120 s default failed
                                  #   every first request after an idle period.
DATA_DIR=/data
CORS_ORIGINS=*
```
Secrets are **never** committed. `.env.example` only.

### Serving notes that constrain the caller
The VLM is a self-hosted vLLM deployment that scales to zero. That shapes the client:
- **Any** request wakes the GPU. `GET /api/v1/health` must therefore *not* probe the VLM, or
  it would pin a container warm and burn GPU-seconds. Derive `vlm.warm` from time-since-last
  -completion against the 900 s scale-down window instead.
- The platform answers `303` while a container boots — the client must follow redirects.
- One image per request, no video. Max 1024 completion tokens (`max_model_len` is 4096 and a
  1280 px image is ~1280 tokens). Pass `temperature=0` explicitly, because the model's own
  `generation_config.json` otherwise applies `repetition_penalty=1.05`.
- `MODAL_PROXY_KEY` / `MODAL_PROXY_SECRET` are unused; auth is the bearer key alone.
