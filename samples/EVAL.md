# Extraction accuracy — full 20-card corpus

Scored with `evaluate.py` against `ground_truth.json`. **Not a claim — rerun it:**

```bash
python evaluate.py <batch.json|export.csv|export.xlsx>
```

## Method

All 20 cards in `cards/` were uploaded as a **single batch** to the live deployment
(`https://leads-34-47-153-95.nip.io`) running `Qwen/Qwen2.5-VL-3B-Instruct` under vLLM on
a serverless Modal **L4**, inside **one warm window** — one cold start (203.8 s), then 20 inferences with no
further cold starts. 20/20 cards succeeded, 154.5 s wall at `VLM_CONCURRENCY=2`.

Scoring replays the model's **raw JSON** (captured in the `Raw` sheet of the batch's own
`.xlsx` export) through the post-processing pipeline that is currently deployed. The
model output is byte-identical to the live run; only normalisation is re-applied, so the
table reflects the code that is actually running rather than a snapshot taken mid-change.
Comparison is normalised per field type by `evaluate.py` — an extractor is not punished
for spacing a phone number differently from the card.

Measured 2026-09-15.

## Per-field

| field | support | precision | recall | F1 | accuracy |
|---|---:|---:|---:|---:|---:|
| `first_name` | 20 | 100.0% | 100.0% | 100.0% | 100.0% |
| `last_name` | 20 | 100.0% | 100.0% | 100.0% | 100.0% |
| `job_title` | 19 | 84.2% | 84.2% | 84.2% | 85.0% |
| `company` | 19 | 94.7% | 94.7% | 94.7% | 95.0% |
| `location` | 19 | 78.9% | 78.9% | 78.9% | 80.0% |
| `phone` | 19 | 84.2% | 84.2% | 84.2% | 85.0% |
| `email` | 19 | 84.2% | 84.2% | 84.2% | 85.0% |
| `website` | 19 | 94.4% | 89.5% | 91.9% | 90.0% |
| **micro** | | **90.2%** | **89.6%** | **89.9%** | **90.0%** |
| **macro F1** | | | | **89.8%** | |

Exact-card rate (all eight fields correct): **70.0%**

## By difficulty

| difficulty | cards | field accuracy | exact-card |
|---|---:|---:|---:|
| clean | 1 | 100.0% | 100.0% |
| edge | 7 | 100.0% | 100.0% |
| degraded | 12 | 83.3% | 50.0% |

`clean` and `edge` cards — including the family-name-first card, the two-phone card, the
no-email card and the bilingual Japanese card — are **100% correct on every field**.
Every remaining error is on a `degraded` card (motion blur, glare, low light, warp),
which is the honest limit of a 3B model on a damaged image, plus three `location` values
that retain a district or building name.

## Known residual errors

- **`location` (4 wrong).** Postcodes and street lines are stripped deterministically
  (`strip_address_detail`). What remains are district/building names with no street
  marker — `Gachibowli Financial District`, `The Old Dispensary`, `Minatomirai` —
  which cannot be distinguished from a city without a gazetteer. One is a genuine
  misread: a blurred multi-address card yielded `Ratingen` instead of `Hamburg`.
- **`card_14` (the deliberately degraded duplicate).** Substantially hallucinated:
  wrong company, phone and domain. It is correctly flagged `blurry`, but scores 0.87 —
  too high — and is not matched as a duplicate of `card_02` because its fields diverge
  too far for any matcher to link them. This is the clearest open weakness.
- **`job_title` and `phone` on blurred cards** account for the rest.

