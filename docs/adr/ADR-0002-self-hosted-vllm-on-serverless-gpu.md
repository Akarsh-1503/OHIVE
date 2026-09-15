# ADR-0002 — Self-hosted vLLM on a serverless GPU, not a hosted VLM API

**Status:** accepted · **Date:** 2026-09 · **Scope:** model serving

## Context

"Deploy a Qwen VLM on a free-tier/equivalent server" admits two readings:

1. Call somebody else's hosted Qwen endpoint from the backend.
2. Actually stand the model up — choose the engine, size the hardware, configure it, secure
   it, and pay for it.

Reading 1 is an afternoon and demonstrates nothing about serving. Reading 2 is the thing being
asked for, and it is where the interesting decisions are. **An API key is not a deployment.**

Having chosen to self-host, the next question is where. A business-card demo has an extremely
bursty load profile: idle for hours, then 25 images in a minute, then idle again. Two shapes
fit that:

- **Always-on GPU** — a rented L4/T4 that exists whether or not anyone is using it.
- **Scale-to-zero serverless GPU** — a container that exists only while requests are in
  flight.

## Decision

Serve **vLLM 0.28.0** (V1 engine, xgrammar structured outputs) on **one serverless L4**, with
`min_containers=0`, `max_containers=2` and a `scaledown_window` of 900 s, exposing an
**OpenAI-compatible `/v1`** behind a bearer key.

## Rationale

**vLLM, because of one feature in particular.** Continuous batching and PagedAttention matter
at 6-way concurrency, but the decisive feature is **grammar-constrained decoding via
xgrammar**: `response_format={"type": "json_schema"}` makes invalid output impossible rather
than unlikely. That single capability removes an entire class of parsing failure from the
product ([ADR-0003](ADR-0003-schema-constrained-decoding.md)).

**OpenAI-compatible, because it makes the whole tier swappable.** The backend uses the stock
`openai` async client. Pointing it at a local vLLM, at llama.cpp, or at OpenAI itself is one
environment variable — which is also what makes the offline `stub` provider a natural third
implementation rather than a testing hack.

**Scale-to-zero, because the load profile is 99 % idle.** The cost shape is decisive:

| | Idle cost | 25-card batch |
|---|---|---|
| Always-on L4 | ~$21/day | ~$0.01 marginal |
| **Scale-to-zero L4** | **$0.00/day** | $0.011 warm, $0.12 cold |

For a demo that a reviewer opens twice, an always-on GPU spends roughly **$630 a month to
serve about a dollar of inference**. That is not a defensible bill for this product.

**One L4 rather than something larger.** 24 GB comfortably holds a 3B VLM plus KV cache, and
it is the cheapest current-generation card with enough memory. `max_containers=2` caps the
worst case that an abusive client can create.

## Alternatives considered

| Option | Why not |
|---|---|
| **A hosted VLM API** (OpenAI, Gemini, Together, Fireworks) | Lower latency, no cold start, better quality — and it answers a different question than the one asked. It also moves per-card cost from $0.0005 to somebody else's price list, and puts a third party between the product and its data. |
| **Always-on rented GPU** (Lambda, RunPod, a GCE `g2-standard-4`) | No cold start, and ~$21/day to be idle. Correct for steady traffic, wrong for this. |
| **CPU inference** (llama.cpp, ONNX Runtime) on the existing VM | Free — and a 3B VLM on 8 vCPUs is minutes per card, with the `raw_text` transcription making it worse. Tested as unviable by arithmetic before it was tested by code. |
| **Modal + `min_containers=1`** | Removes the cold start for ~$21/day. One line away if the trade ever changes. |
| **TGI or a raw `transformers` server** | Both work. Neither offers grammar-constrained decoding as cleanly, which was the deciding feature. |

## Consequences

**Positive.**
- The GPU costs **nothing** at idle. A reviewer can leave the demo running for a month.
- One L4 handles the whole concurrency budget at 0.50–0.67 img/s.
- The model tier is genuinely owned: engine version, flags, quantisation, memory layout and
  auth are all in this repository and all deliberate.
- The whole tier is swappable through one environment variable — including for an offline
  stub, which is what lets the entire test suite run with no GPU.

**Negative — and it is a big one.**
- **Cold start is 187–237 s (median ~210 s).** The first request after four idle minutes is
  slow enough that an unprepared user will assume the app is broken. Mitigations —
  `--enforce-eager`, a background warm-up call, a *Cold* pill in the UI, a 360 s cold timeout
  — reduce the surprise but not the wait. This is [limitation #1](../07-limitations.md).
- Every caller must tolerate a 300 s+ timeout and follow the `303` returned while a container
  boots.
- The endpoint is publicly routable, so the bearer key is the only thing between the URL and
  the credit balance — which is why the `/invocations` hole in vLLM's own auth middleware had
  to be closed by hand.
- Operating a GPU deployment is work the product carries: pinned revisions, cache volumes,
  flag tuning, and a cost model to watch.

**The trade in one line:** this spends 210 seconds, once per idle period, to spend zero
dollars the rest of the time.
