# ADR-0004 — Deterministic post-processing and a review queue, not raw model output

**Status:** accepted · **Date:** 2026-09 · **Scope:** extraction quality

## Context

Schema-constrained decoding ([ADR-0003](ADR-0003-schema-constrained-decoding.md)) guarantees
the *shape* of the model's answer. It guarantees nothing about its truth. A VLM will happily
return:

```jsonc
{ "phone":   { "value": "080 4555 0118", "confidence": 0.94 },   // no country code
  "email":   { "value": "aarav@northwind.corn", "confidence": 0.91 },  // rn -> m
  "company": { "value": "Head of Platform Engineering", "confidence": 0.88 },  // copied the title
  "website": { "value": "HELIIXMERIDIAN.EXAMPLE", "confidence": 0.86 } }       // misread caps
```

Every one of those is well-formed, confidently scored and wrong — and the last three are real
observations from this corpus, not invented examples.

Two properties of model-reported confidence make it unusable on its own:

1. **It is a per-field reading certainty, computed in isolation.** The model has no mechanism
   for noticing that the email domain contradicts the printed website.
2. **It is systematically overconfident exactly where the image is worst.** A model misreading
   a blurred card is *fluent*, and fluency is what the score reflects.

So the design question is: what does the system do with an answer it cannot verify?

## Decision

Layer deterministic processing on top of every model response, in four stages, and route the
result into a **`needs_review` queue** rather than accepting or rejecting it.

1. **Normalise** — one canonical form per field: names split with titles, particles and
   suffixes handled; phones to E.164 via `phonenumbers` using the card's own address as the
   region hint; emails validated with `email-validator`; URLs reduced to a bare host.
2. **Cross-validate** — use agreement *between* fields to move confidence up or down.
3. **Score the record** — a weighted mean over populated fields, multiplied by quality
   penalties derived from image metrics measured before the model call.
4. **Queue** — below `CONFIDENCE_THRESHOLD`, status becomes `needs_review`. Nothing is
   discarded, ever.

Mechanics and the full rule table: [03 — the extraction pipeline](../03-extraction-pipeline.md).

## Rationale

### Why cross-field validation is the highest-value layer

A business card prints the same fact more than once. The name is in the name line *and* in the
email local part. The company is a name *and* a domain, in both the email and the URL. The
country is an address *and* a phone country code.

**Two independent readings of the same fact agreeing is real evidence** — much stronger than
either reading's own certainty — and disagreeing is a real warning. `priya.raghavan@` beside
`Priya Raghavan` corroborates three fields at once. A `+1` number beside a Bengaluru address
means one of the two was misread, and since it is not knowable which, both are discounted and
the record is flagged.

This is information the model structurally cannot use: it scores each field in isolation, and
the corroboration lives in the *relationship* between fields.

### Why deterministic and not "ask the model to check itself"

A second model pass costs another 6–11 s and another ~$0.0005 per card, is itself unverifiable,
and cannot be unit-tested. `phonenumbers` either parses `+91 80 4555 0118` or it does not, and
that behaviour is pinned by a table-driven test. **Determinism here is not conservatism, it is
testability** — these rules are the most heavily tested code in the repository, and they are
the reason a quality regression shows up in CI rather than in an export.

### Why repairs must be corroborated

`@gmall.com` is syntactically perfect, so validity alone cannot decide whether to fix it. A
repair that quietly turns a visibly-wrong value into a plausibly-wrong one is **worse than no
repair**: a reviewer can fix a flagged bad address, but will never notice a confidently wrong
one. So a repair is accepted only when a second independent piece of the card agrees — the
printed website, the company name, or a ubiquitous consumer domain — and an address that
already parses is never overruled by a speculative character swap.

### Why a review queue beats accept/reject

A hard threshold forces a bad choice on every uncertain card: ship it silently into the CRM, or
throw away a lead the user paid for. Both destroy value. A queue is the third option:

- **Nothing is discarded.** Every card the model read appears in the UI and in the export,
  with its flags.
- **The reviewer sees *which* fields are weak and why**, beside the original image. The
  correction loop is seconds, not minutes.
- **The threshold becomes a business dial rather than a correctness boundary.** Raise it and
  more cards get human eyes; lower it and fewer do. Neither setting can lose data.
- **Human edits are ground truth.** `PATCH` pins each edited field to 1.0 and re-scores, so a
  card climbs out of the queue as it is corrected — and correctly *stays* in when only part of
  it has been fixed.

The same reasoning drives "floor, never delete": an unparseable phone stays on the record
exactly as printed, at a capped score. Deleting it would destroy the only copy of information a
human could have salvaged in two seconds.

## Alternatives considered

| Option | Why not |
|---|---|
| **Trust the model's output and confidence** | Ships confidently wrong data into a CRM. The failure is invisible, which makes it the worst option on the list. |
| **Hard accept/reject at a threshold** | Either silently ships bad leads or silently bins good ones. No setting avoids both. |
| **A second LLM pass as validator** | Doubles cost and latency, unverifiable, untestable. |
| **Ask the model for a self-critique in the same response** | Cheaper, and self-reported reliability from the same forward pass that produced the error is worth very little. |
| **Reject anything without a perfect score** | On real photographs almost nothing scores perfectly. The product becomes unusable. |

## Consequences

**Positive.** Malformed phones and emails are caught deterministically and testably. Confidence
means something because it reflects evidence, not fluency. Nothing is ever silently discarded.
The extraction rules are versioned, reviewable code rather than prompt folklore.

**Negative.**
- **More code to own.** `normalise.py` is 420 lines of string handling, and string handling is
  where edge cases live. It is the most-tested module for exactly that reason.
- **The rules encode assumptions.** Given-name-first ordering, a curated country/city table
  rather than a gazetteer, a fixed post-nominal list. Each is a known failure mode, listed in
  [07 — limitations](../07-limitations.md).
- **The quality penalty is flat per flag, not weighted by severity.** This is the design's
  real hole: a confidently misread, heavily blurred card scored **0.88** and sailed past the
  threshold. The fix — mapping the continuous focus score onto a continuous multiplier — is
  identified and unimplemented.
- **A review queue only pays off if someone works it.** A product where nobody opens the queue
  is a product that ships unreviewed data with extra steps.
