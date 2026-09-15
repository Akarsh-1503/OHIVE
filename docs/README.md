# LeadForge documentation

**Live: https://leads-34-47-153-95.nip.io** · Start with the [project README](../README.md)
if you have not read it — it has the quickstart and the requirements-traceability table.

Every number in these documents is measured. Where a figure could not be measured it is
labelled `estimated` in the text. Diagrams are Mermaid, so they render in any Git host with
no binary assets.

## 60-second tour

LeadForge turns a pile of business-card photos into structured, reviewed, exportable leads.
Three tiers: a Next.js UI, a FastAPI service that owns everything deterministic, and a
self-hosted `Qwen2.5-VL-3B-Instruct` served by vLLM on a scale-to-zero L4 GPU.

The design decision the whole system is organised around is this: **a vision-language model
is an excellent reader and a poor validator.** So the model is asked to do exactly one job —
transcribe what is printed, under a JSON schema it cannot violate — and every judgement about
whether a value is *trustworthy* is made afterwards by deterministic code: phone parsing,
email validation, name splitting, cross-field agreement, duplicate detection. Instead of a
hard accept/reject, low-scoring leads land in a `needs_review` queue where a human fixes them
against the original image in a few seconds.

If you read one document, read
[**03 — the extraction pipeline**](03-extraction-pipeline.md). If you read two, read
[**07 — limitations**](07-limitations.md).

## Index

| | |
|---|---|
| [01 — Architecture](01-architecture.md) | System diagram, request lifecycle, data flow, deployment topology, and the AWS equivalents of every GCP resource |
| [02 — Model serving](02-model-serving.md) | Why self-hosted vLLM, how the scale-to-zero L4 is configured and hardened, cold-start anatomy, cost model |
| [03 — Extraction pipeline](03-extraction-pipeline.md) | Schema-constrained decoding → normalisation → cross-field validation → review queue → dedup, and why each layer has to exist |
| [04 — API](04-api.md) | Every endpoint with a real `curl` and a real response, copied from a running instance |
| [05 — Frontend](05-frontend.md) | UI architecture, the single SSE hook, the render-avoidance strategy, and the motion design rationale |
| [06 — Performance](06-performance.md) | Benchmark methodology precise enough to re-run, measured numbers, test environment |
| [07 — Limitations](07-limitations.md) | The failure modes, named. Including the ones that are embarrassing |
| [08 — Security & cost](08-security-and-cost.md) | Threat model of a public demo, abuse limits, what it costs to run |

## Decision records

One per genuinely contested fork in the road — not one per file.

| | |
|---|---|
| [ADR-0001](adr/ADR-0001-qwen2.5-vl-3b-over-larger-variants.md) | Qwen2.5-VL-**3B** over 7B/72B: accuracy vs cold start vs GPU cost on a fixed budget |
| [ADR-0002](adr/ADR-0002-self-hosted-vllm-on-serverless-gpu.md) | Self-hosted vLLM on a serverless GPU vs a hosted VLM API — and the scale-to-zero trade |
| [ADR-0003](adr/ADR-0003-schema-constrained-decoding.md) | Schema-constrained decoding instead of prompt-and-hope JSON parsing |
| [ADR-0004](adr/ADR-0004-deterministic-post-processing-and-review-queue.md) | Deterministic post-processing + cross-field validation, and why a review queue beats accept/reject |
| [ADR-0005](adr/ADR-0005-sse-over-websockets-and-polling.md) | SSE over WebSockets or polling for batch progress |
| [ADR-0006](adr/ADR-0006-sqlite-and-local-disk-over-postgres.md) | SQLite + local disk over Postgres + object storage, at this scale |

## Related reading in the repository

| | |
|---|---|
| [`../backend/README.md`](../backend/README.md) | Backend service reference |
| [`../frontend/README.md`](../frontend/README.md) | Frontend reference, including the in-process mock backend |
| [`../infra/modal/README.md`](../infra/modal/README.md) | The GPU deployment: deploy steps, auth hardening, tuning notes, cost |
| [`../samples/README.md`](../samples/README.md) | The 20-card ground-truth corpus, its deliberate traps, and the scorer |
| [`../../infra/README.md`](../../infra/README.md) | Public deployment runbook for the live host |
| [`../../_contracts/assignment1-api.md`](../../_contracts/assignment1-api.md) | The frozen API contract both tiers implement |
