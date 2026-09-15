# 07 — Limitations

This document is deliberately uncomfortable. Every item is a real weakness of the system as
shipped, described precisely enough to reproduce, with what it would take to fix it. A
reviewer should be able to trust that the problems they find are already on this list.

Ordered roughly by how much they would matter to someone actually using the product.

---

## 1. The first request after an idle period takes ~210 seconds

**What happens.** The GPU container scales to zero after 900 s without traffic. The next
request pays a full cold boot: **187–237 s, median ~210 s**, and 258–300 s on a first-ever
boot with empty caches. A reviewer who opens the live URL, drops in 25 cards and hits Extract
will sit there for roughly four minutes before the first lead appears.

**Why it is like that.** Scale-to-zero means the endpoint costs **$0** while nobody is using
it. On a demo that is idle 99 % of the time, the alternative — keeping an L4 warm — is roughly
$21/day of GPU on top of the $10.58/day host. The trade was made knowingly:
[ADR-0002](adr/ADR-0002-self-hosted-vllm-on-serverless-gpu.md).

**What is done about it.** The `scaledown_window` is 900 s, so this is a once-per-session cost
rather than a once-per-batch one: a reviewer who uploads a second batch within 15 minutes of
the first pays no cold start at all, for ~$0.20 of idle GPU. `--enforce-eager` takes ~35 s off
the median. `POST /vlm/warmup`
fires a one-token completion in the background so the boot overlaps with the user's
drag-and-drop instead of following it. `/health` reports `warm: false` so the UI can show a
*Cold* pill and an explicit warning rather than a spinner that looks hung. `VLM_TIMEOUT_S`
defaults to 300 s and a separate 360 s cold timeout applies until the first success, because
the original 120 s default failed *every* first request.

**What would actually fix it.** `min_containers=1` — one line, and the problem disappears at a
known price. Or a small always-warm CPU-served fallback model for the first card while the GPU
boots, which is more engineering than this deserves.

---

## 2. Family-name-first ordering is split wrongly

**What happens.** `split_name` assumes given-name-first. A card printing `Tan Wei Ming`
returns:

```
first_name: "Tan Wei Ming"
last_name:  "Ming"
```

Both are wrong. The correct split is `Wei Ming` / `Tan`. This affects Chinese, Korean,
Vietnamese, Hungarian and Japanese cards that print the family name first — and the sample
corpus contains exactly this case (`card_07`, tagged `family_name_first`), so it is a known
failure, not a hypothetical one.

**Why it is not fixed.** Name order cannot be inferred from the string alone. `Tan Wei Ming`
and `Wei Ming Tan` are the same three tokens.

**What would fix it, and the annoying part.** *The evidence is already on the card and already
parsed.* The email local part on such a card is typically `weiming.tan@` — which is the family
name in the trailing position. Layer 3 already compares the local part against the name for
corroboration; extending that comparison to detect a *reversed* match and swap the fields is a
contained change to `cross_validate`, with a confidence penalty when it fires so the swap is
visible to the reviewer. It is the single highest-value unimplemented item in the pipeline,
and it is not implemented.

Secondary name failures in the same family: mononyms get `last_name: null`; a hyphenated
double-barrelled surname where only one half appears in the email will not corroborate; and
post-nominals outside the curated list (`_POST_NOMINALS` is about 30 entries) end up attached
to the surname.

---

## 3. A badly degraded card can be confidently, invisibly wrong

**This is the weakness that matters most, because it is the one a user would not catch.**

**What happens.** The corpus contains `card_14`, a heavily blurred and warped re-capture of
`card_02`. Run through the live model it produced substantially **hallucinated** fields —
wrong company, wrong phone, wrong domain — and scored **0.88 overall confidence**, comfortably
above the 0.65 review threshold. It was correctly flagged `blurry`. It was *not* flagged as a
duplicate of its own source card, because the extracted fields had diverged so far that
neither the email, the phone nor the fuzzy name+company match fired.

So the worst card in the batch was presented as one of the good ones, and its relationship to
the card it duplicates was lost.

**Why the confidence model let it through.** Two compounding causes:

1. **The model's per-field scores stay high because it is confidently misreading.** A VLM
   asked "how sure are you?" reports fluency, not correctness. There is no mechanism in the
   model for knowing it invented a company name.
2. **Quality flags apply a flat multiplicative penalty per flag** — `blurry` is ×0.90 whether
   the image is slightly soft or unreadable. A flat factor cannot express *severity*, so a
   0.97 raw score times 0.90 is still 0.87.

**What would fix it.** The focus score is already a continuous number (in-focus 38–59,
3 px blur 8–9). Weighting the penalty by measured severity rather than by flag presence —
mapping focus score onto a continuous multiplier — would push this card well under the
threshold without touching anything that reads cleanly. A second, cheaper mitigation: when a
card's quality metrics are bad *and* its extracted fields fail every cross-field corroboration
check, that combination is itself strong evidence of hallucination and should floor the score
outright. Neither is implemented.

A blunter option, which is real: refuse to extract below a focus floor and ask the user to
re-photograph the card. Returning nothing is sometimes the correct product answer, and this
system never does it.

---

## 4. Every quality threshold is calibrated on synthetic cards

**What happens.** `FOCUS_SCORE_FLOOR = 30`, `CONTRAST_RANGE_FLOOR = 90`,
`DARK_MEAN_CEILING = 70`, `MIN_USABLE_LONG_EDGE = 800`, `CONFIDENCE_THRESHOLD = 0.65`,
`DUPLICATE_FUZZY_THRESHOLD = 90`, the per-flag penalty table and the eight field weights —
all of them were tuned against **rendered cards with programmatically applied degradations**
(Gaussian blur at known radii, synthetic glare, simulated JPEG damage), not against a corpus
of real photographs taken by real people in real trade-show lighting.

**Why that is a problem and not just a caveat.** Synthetic blur is *uniform*; real blur is
directional, depth-dependent and often affects only part of the card. Synthetic low light is a
clean exposure shift; real low light brings sensor noise, mixed colour temperature and
specular highlights from overhead lighting. A threshold tuned on the first kind can be
systematically wrong on the second, and nothing in this repository would reveal it. The
calibration values quoted in `preprocess.py` are honest about what they came from; they are
not honest about what they will do to a photo taken in a conference hall.

**What would fix it.** A few hundred real card photographs with hand-labelled ground truth,
then re-fit the thresholds and re-measure. That is a data-collection project, not a code
change, which is precisely why it has not happened.

Related: the corpus is **20 cards from 7 countries**. Cards from countries it does not cover,
scripts it does not contain, and layouts nobody thought of are all untested.

---

## 5. The live accuracy figure rests on five cards

**What happens.** The headline accuracy — 28/32 fields (88 %) on non-degraded cards, 30/40
(75 %) including the degraded one — comes from **five cards run through the live model**. The
ground-truth corpus holds 20; fifteen of them have never been scored against the real GPU,
only against the stub.

**Why.** GPU budget and cold starts: each full-corpus run is a boot plus 20 inferences, and
the numbers were gathered while the endpoint was still being tuned.

**What that means for the percentages.** One card is 2.5 % of the "all five" figure. The
confidence interval on 88 % from a sample of 32 field comparisons is wide enough that the true
value could plausibly be anywhere from the mid-seventies to the mid-nineties. **Read the
number as "it works well on clean cards and struggles on badly degraded ones", not as a
precise score.**

**What would fix it.** One command, roughly $0.15 of GPU: upload `samples/cards/` as a batch,
export the CSV, run `python samples/evaluate.py export.csv`. It is the first thing to do with
more budget.

---

## 6. No authentication and no rate limiting on the public demo

**What happens.** Anyone with the URL can upload 25 cards at a time, as often as they like,
and every upload spends real GPU credit. There is no login, no API key, no per-IP quota, no
CAPTCHA and no upload budget. `CORS_ORIGINS` defaults to `*`.

There is also no authorisation *between* users: batch ids are unguessable 128-bit hex, but
anyone who obtains one can read every lead in it, download its export and PATCH its records.
**Security by unguessable identifier is not access control.**

**What is protected.** The GPU endpoint itself is bearer-authenticated, including the
`/invocations` route vLLM's own middleware leaves open (see
[02](02-model-serving.md#auth-the-route-vllm-leaves-open)), so a stranger cannot bypass the API
and drive the model directly. Upload size, file count and content type are enforced, and an
oversized body is rejected from `Content-Length` before it is read. The blast radius of abuse
is therefore *cost*, not compromise.

**What would fix it.** For a demo: a per-IP token bucket in the reverse proxy and a daily
batch cap. For anything real: authentication, per-tenant isolation in the data model, and a
signed URL scheme for exports. None of it is there.

---

## 7. Single replica, single node, no high availability

**What happens.**

- **SQLite** in `DATA_DIR`, one file, WAL mode. Fine for the load, but it means one API
  process.
- **The SSE event bus is in-process.** A second replica would serve clients that never receive
  events for batches processed by the first.
- **Uploaded images are on local disk.** A second replica cannot serve a card image it does not
  have.
- **One VM.** No load balancer, no second zone. `docker compose restart`, a kernel upgrade or a
  zone incident is downtime.
- **No backups.** The volume is not snapshotted. If the disk goes, the batches go.

**And the one that will bite first:** if the API restarts mid-batch, cards that were in flight
are left in `processing` and are **not resumed automatically**. Everything already persisted
survives; the stuck cards need `POST /batches/{id}/retry` with their `card_ids`. There is no
reaper that notices a `processing` card whose worker no longer exists.

**Why it is like that.** At one concurrent user, Postgres + Redis + object storage is three
more failure modes and three more things to explain, in exchange for nothing. See
[ADR-0006](adr/ADR-0006-sqlite-and-local-disk-over-postgres.md).

**What would fix it.** The module boundaries were drawn for exactly this: swapping `store.py`
for Postgres and `EventBus` for Redis pub/sub, and putting images in object storage, is a
change to two modules rather than a rewrite. Plus a startup sweep that re-queues any card left
in `processing`.

---

## 8. No PII retention policy

**What happens.** A business card is personal data — a named individual's employer, job title,
phone number, email address and office location. LeadForge stores the uploaded image **and**
the extracted fields **and** the model's raw JSON payload, indefinitely, in a volume on one VM.
There is:

- no retention period and no deletion job;
- no `DELETE /batches/{id}` endpoint — **a user cannot delete their own upload**;
- no encryption at rest beyond whatever the cloud disk provides;
- no audit log of who read a lead;
- no data-processing agreement, no privacy notice, no consent capture;
- no region pinning, and the model endpoint is in a different jurisdiction from the host.

**Why it matters more than the usual boilerplate.** The product's whole purpose is bulk-ingesting
other people's contact details. Under GDPR-style regimes this is exactly the kind of processing
that needs a lawful basis, a retention limit and a deletion path. **Shipping this as-is to real
users would be a compliance problem, not a nice-to-have.** The demo is only defensible because
the corpus is entirely synthetic — every name, company, domain (`.example`) and phone number in
`samples/` is invented and undialable.

**What would fix it.** A `DELETE /batches/{id}` that removes rows, images and thumbnails; a TTL
sweep (batches older than N days); a documented retention period; and encryption at rest. The
first two are an afternoon each.

---

## 9. Extraction limits inherited from the model and the contract

- **Quality is bounded by Qwen2.5-VL-3B.** Dense, low-contrast, heavily stylised or
  handwritten cards are where the review queue earns its place. Handwriting in particular is
  not something this model does well.
- **One phone field.** The contract has a single `phone`, so a card printing mobile, office and
  fax keeps only the first — the others survive in `raw_text` but not as structured data.
  `card_07` in the corpus exists to make this visible.
- **Bilingual and non-Latin cards are partly lossy.** The model transcribes both scripts into
  `raw_text`, but the structured fields hold one rendering. Nothing reconciles a Japanese name
  block against its romanised counterpart.
- **`region_from_location` is a curated table, not a gazetteer** — about 80 countries and 70
  cities. An address in a city it does not know yields no region hint, so a phone number
  printed without a country code falls back to `DEFAULT_PHONE_REGION` (`US`) and may be
  mis-parsed or rejected.
- **QR codes are not decoded.** `card_10` carries a real MECARD payload that
  `cv2.QRCodeDetector` can read after auto-levelling. That would be a cheap, *independent*
  corroboration source for the cross-validation layer — arguably the best one available — and
  it is unused.
- **Only the first page of a PDF is rendered.** A multi-page scan silently loses pages 2+.
- **Duplicate anchoring is by completion order, not upload order.** Under concurrency the
  "first-seen" card that becomes the anchor for a duplicate group may not be the first one the
  user uploaded. Harmless, but surprising.

---

## 10. Operational and repository-level gaps

- **No metrics, no tracing.** Structured JSON logs to stdout with a request id, and that is
  all. There is no Prometheus endpoint on the API, no dashboard, no alerting. Nobody would know
  the model endpoint had started failing until someone uploaded a card.
- **The host costs $10.58/day whether or not anyone uses it.** Unlike the GPU, the VM does not
  scale to zero. Stopping it drops that to $0.68/day and keeps the IP, hostnames and
  certificates.
- **Images are built on the VM**, so there is no image registry and therefore no version to
  roll back to. Fine for a demo host, wrong for production.
- **The default developer stack runs `VLM_PROVIDER=stub`**, which produces convincing but
  entirely synthetic leads. The health pill in the UI shows the provider, and the export's
  Summary sheet records it, but a hurried reviewer could still mistake stub output for model
  output. That is a deliberate trade for a zero-credential quickstart, and it is stated at the
  top of the README rather than hidden.
- **Two pieces of dead configuration.** `MODAL_PROXY_KEY` / `MODAL_PROXY_SECRET` are wired
  through the client but unused by the current endpoint (auth is the bearer key alone), and the
  deployment compose file sets `API_INTERNAL_BASE` for the frontend, which the frontend never
  reads.
- **`./data` bind mounts are not portable.** The developer stack uses a named volume because a
  bind mount takes host ownership (breaking the image's non-root uid on Linux) and because
  Docker Desktop refuses to share some host paths outright — verified on a repository under
  `~/Desktop`, which fails with `operation not permitted`. The bind-mount variant is documented
  in `docker-compose.yml` for anyone who wants the SQLite file on the host.

---

## What two more weeks would buy

In the order I would actually do them:

1. **Severity-weighted quality penalties** (limitation 3) — the highest-value change in the
   system. A day, including re-calibration.
2. **Reversed-name detection from the email local part** (limitation 2). A day, with tests.
3. **Score the full 20-card corpus against the live model** (limitation 5) and publish the
   per-field precision/recall table rather than a single percentage. An hour and $0.15.
4. **`DELETE /batches/{id}` plus a TTL sweep** (limitation 8). Two days including the UI.
5. **A per-IP token bucket and a daily batch cap in Caddy** (limitation 6). Half a day.
6. **A startup sweep that re-queues cards stuck in `processing`** (limitation 7). Hours.
7. **QR-code decoding as a corroboration source** (limitation 9) — the cheapest genuine
   accuracy win available, because a MECARD payload is machine-readable ground truth printed on
   the card itself.
8. **Collect 200 real card photographs and re-fit every threshold** (limitation 4). The
   largest item on the list, and the one that would most change what the product actually does.
