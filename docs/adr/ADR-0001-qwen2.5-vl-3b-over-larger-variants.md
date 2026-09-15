# ADR-0001 — Qwen2.5-VL-3B over the 7B and 72B variants

**Status:** accepted · **Date:** 2026-09 · **Scope:** model selection

## Context

The brief names Qwen and a "free-tier or equivalent" server, which fixes the family and caps
the budget. Within that family the realistic candidates on one 24 GB L4 are:

| Variant | Weights | Fits an L4? |
|---|---|---|
| Qwen2.5-VL-**3B**-Instruct | ~7 GB (bf16) | yes, with ~15 GB left for KV cache |
| Qwen2.5-VL-7B-Instruct-AWQ | ~6 GB (4-bit) | yes, ~13 GB left |
| Qwen2.5-VL-7B-Instruct | ~16 GB (bf16) | tight, little KV headroom |
| Qwen2.5-VL-72B | ~145 GB | no — multi-GPU only |

The workload is narrow and well characterised: **a small amount of large, high-contrast text
on a rectangular card**, with a fixed output schema. This is much closer to OCR with layout
understanding than to open-ended visual reasoning — and that is exactly the regime where model
scale saturates early.

The deployment scales to zero, which changes the arithmetic in a way that is easy to miss:
**model size is paid twice.** Once per token during inference, and once per cold start, because
a larger model takes longer to load and longer to warm.

## Decision

Serve **`Qwen/Qwen2.5-VL-3B-Instruct`**, revision pinned
`66285546d2b821cf421d4f5eb2576359d3770cd3`, in bf16 on a single L4.

## Rationale

**Measured, not assumed.** On the three fixed benchmark fixtures — including one deliberately
rotated, blurred and unevenly lit to stand in for a phone photo — 3B returned **every field
correct on every run**, with a faithful `raw_text` transcription. Against the ground-truth
corpus, 88 % of fields on non-degraded cards, with name, company, phone, email and website
correct on every one of them.

There is no headroom for a larger model to demonstrate. The residual errors are not cases
where the model failed to *reason*; they are cases where the pixels do not contain recoverable
information (a heavily blurred card) or where the prompt asked for the wrong thing (the
location/postcode issue, fixed in the prompt). Neither is solved by parameters.

**What 7B-AWQ would have cost.** Roughly 2× per-card latency (≈12–22 s warm instead of
6–11 s), plus a longer cold start on an endpoint where the cold start is already the single
worst user-visible number. Quantisation adds its own quality question on a task whose whole
point is character-level fidelity: a 4-bit model misreading one character of a domain is a
regression that this corpus is too small to detect reliably.

**Cold start is the binding constraint.** 3B loads from the cache volume in ~4 s of a ~210 s
boot. A model three times the size makes the worst number worse for a quality gain that cannot
be measured.

**The revision pin is part of the decision.** An upstream re-upload to the same tag would
otherwise silently change what a deployed endpoint serves, and the first symptom would be a
quality regression nobody can reproduce.

## Alternatives considered

| Option | Why not |
|---|---|
| **Qwen2.5-VL-7B-Instruct-AWQ** | ~2× latency and a longer boot for an unmeasurable gain. Remains the documented upgrade path: two constants in `qwen_vlm.py`. |
| **Qwen2.5-VL-72B** | Multi-GPU. Out of budget by an order of magnitude and absurd for reading eight fields off a card. |
| **A hosted frontier VLM** (GPT-4o, Gemini, Claude) | Would very likely score higher. But the brief asks to *deploy a Qwen VLM*, and an API key is not a deployment. See [ADR-0002](ADR-0002-self-hosted-vllm-on-serverless-gpu.md). |
| **Classical OCR** (Tesseract, PaddleOCR) + rules | Gives text without structure. A card's semantics are *spatial* — which line is the title, which is the company — and a rule engine over OCR boxes is a large amount of brittle code to approximate what the VLM does natively. Considered and rejected on effort-to-quality. |

## Consequences

**Positive.** Cold start stays under four minutes and warm inference under twelve seconds a
card. The whole endpoint runs on one L4 at $0.87/hour of container wall-clock, and $0 at idle.

**Negative.** Extraction quality is capped by a 3B model. Dense, low-contrast, heavily
stylised and handwritten cards are where the review queue earns its place — and a degraded card
can be confidently wrong ([limitation #3](../07-limitations.md)). That ceiling is structural,
and the mitigation is the post-processing layer rather than a bigger model.

**Reversible.** Two constants and a redeploy. `VLM_MODEL` must be updated to match, because
`--served-model-name` is the full Hugging Face id.
