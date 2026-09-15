# ADR-0003 — Schema-constrained decoding, not prompt-and-hope JSON

**Status:** accepted · **Date:** 2026-09 · **Scope:** model interface

## Context

The pipeline needs a structured object per card: eight nullable fields, each with a confidence
score, plus a full transcription. The conventional approach is to describe that shape in the
prompt, ask for "only JSON", and then parse defensively — stripping code fences, regexing for
braces, retrying on failure.

That approach fails in ways that are individually small and collectively expensive: a markdown
fence, a trailing comma, a chatty preamble ("Here is the extracted data:"), a field the model
decided to omit because it was null, the string `"null"` instead of `null`, a confidence
returned as `"high"`. Each one is a retry — more latency, more GPU-seconds, and a nonzero
chance of the same failure again. On a 25-card batch a 4 % malformed rate means one card
arrives late or not at all, every time.

vLLM's V1 engine ships **xgrammar**, which constrains sampling to tokens that keep the output a
valid instance of a supplied JSON Schema.

## Decision

Pass `response_format={"type": "json_schema", "json_schema": {…, "strict": true}}` on every
call. Keep a tolerant parser and a single repair turn as a fallback for servers that ignore the
parameter.

## Rationale

**The failure mode is eliminated, not reduced.** With guided decoding, a malformed response is
not unlikely — it is **not in the sampling distribution**. The grammar is compiled once and the
logits are masked at each step. There is no probabilistic tail to retry against.

**It costs effectively nothing.** Grammar compilation is per-schema, not per-request, and the
masking overhead is negligible next to a ~1280-token image prefill.

**It makes nullability real.** `{"type": ["string", "null"]}` with
`additionalProperties: false` means the model can emit an actual `null`. That matters more than
it sounds: on a card printing only a name and an email, six of eight fields *must* come back
null, and a model that invents a company there is worse than one that returns nothing. Under a
prompt-only regime, "return null" competes with the model's pull toward producing a plausible
value; under a grammar it is simply a legal token sequence and inventing is not required.

**The schema is inlined eight times rather than using `$ref`.** Guided-decoding backends have
uneven `$ref` support, and a few hundred extra bytes on the wire is a cheap price for a schema
that compiles on every backend. Verified against the live deployment: xgrammar compiles it and
the model returns real `null`s, not the string `"null"`.

**The fallbacks are belt and braces, not the plan.**
1. A tolerant parser: strip code fences, then find the outermost balanced `{…}` while
   correctly ignoring braces inside JSON strings.
2. One repair turn: hand the model its own bad output back with an instruction to emit only the
   object. This is cheaper and far more reliable than a blind resample, because the model can
   see what it got wrong.

Both exist so that pointing `VLM_BASE_URL` at a server *without* guided decoding degrades
gracefully rather than breaking the product.

## Alternatives considered

| Option | Why not |
|---|---|
| **Prompt + defensive parsing + retries** | The industry default, and the thing this replaces. Trades a solved problem for a permanent tax in latency and GPU-seconds. |
| **Function/tool calling** | Equivalent expressiveness, more machinery, and support varies more across OpenAI-compatible servers than `response_format` does. |
| **Outlines or LM Format Enforcer as a library** | Would require owning the sampling loop. vLLM already integrates xgrammar at the server. |
| **Free-form text + a parsing model** | A second model to clean up the first. More cost, more latency, more failure modes. |
| **Regex extraction from `raw_text`** | Exactly the brittle rule engine that using a VLM was supposed to avoid. |

## Consequences

**Positive.** Zero parse failures observed against the live endpoint. No retry budget spent on
formatting. The schema is a single source of truth shared by the prompt, the parser and the
tests. Adding a field is a one-line schema change.

**Negative.**
- **The schema is a contract with the model.** Changing it changes what the model emits, so it
  needs the same care as an API change.
- **A grammar constrains form, not truth.** The output is guaranteed parseable and guaranteed
  to have eight fields with confidences — and says nothing about whether any of them is
  correct. That is precisely why every layer in
  [03 — the extraction pipeline](../03-extraction-pipeline.md) exists downstream of it.
- A subtle risk of over-constraining: forcing a shape can push a model toward filling a field
  it should have left null. Mitigated by making `null` a first-class value in the schema and by
  a prompt that says so explicitly.
- The dependency on xgrammar is real. It is soft-failed by the tolerant parser and the repair
  turn, both of which are tested against a scripted server that ignores `response_format`.
