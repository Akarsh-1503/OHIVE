# 08 — Security and cost

Two questions about a publicly reachable demo that runs a GPU: **what can a stranger do to
it**, and **what does it cost while they do**.

---

## Threat model

The realistic adversary is not a targeted attacker. It is someone who finds the URL and either
plays with it or automates it. The asset most exposed is therefore **metered GPU credit**,
followed by **the personal data on uploaded cards**.

| Asset | Exposure | Control in place | Residual risk |
|---|---|---|---|
| GPU credit | Anyone with the app URL can trigger inference | Upload caps (25 files, 12 MB each), whole-body `413` from `Content-Length`, `VLM_CONCURRENCY=6` bound on in-flight requests, `max_containers=2` ceiling on the GPU side | **No rate limiting.** A determined scripter can spend credit. The cap is `max_containers`, not policy. |
| The model endpoint | Publicly routable | Bearer key on **every** route except `/health` and `/metrics`, including `/invocations` and `/tokenize` | Key compromise. It is not rotated automatically. |
| Uploaded card images and extracted leads | Readable by anyone holding the batch id | 128-bit unguessable ids; TLS in transit | **No authorisation.** An id that leaks (browser history, a shared link, a screenshot) grants full read and write on that batch. |
| The host | Ports 80/443 open to the world | SSH is **not** internet-reachable — tcp:22 only from Google's IAP range; dedicated VPC, not the permissive `default` network; containers run as non-root users | Container escape, unpatched host. No automatic patching. |
| Secrets | — | Nothing but `.env.example` in the repository; real values in a Modal Secret and the host's git-ignored `.env`; `.gitignore` covers `.env*`, `data/`, `*.pem` | Operator error. |

### What is actually enforced

**Upload validation, before the bytes are read.** A `Content-Length` above the whole-batch
ceiling is rejected by middleware with `413 PAYLOAD_TOO_LARGE`. Per file: content type must be
one of the six accepted types *or* the extension must match (browsers mislabel HEIC as
`application/octet-stream`, so one of the two must agree); size is checked while streaming to
disk and the partial file is unlinked on breach; empty files are rejected. Count is capped at
`MAX_FILES_PER_BATCH`.

**Decoder hardening.** `Image.MAX_IMAGE_PIXELS` is raised only to 120 MP — enough to accept a
phone panorama uploaded by mistake, still a decompression-bomb guard. PDFs render page one
only, at a bounded scale. Decode failures raise a typed error and fail that card, not the
batch.

**Non-root everywhere.** The API image creates and runs as uid 10001; the frontend image runs
as `nextjs`. Neither needs a GPU, a privileged capability or a host mount beyond `DATA_DIR`.

**The `/invocations` hole, closed.** vLLM's own `--api-key` middleware guards only `/v1`,
`/v2`, `/inference` and `/cohere`. `/invocations` — a SageMaker-compatibility route that
accepts the same body as `/v1/chat/completions` — is left wide open, which on a metered GPU is
a direct path from "someone has the URL" to "someone is spending the budget". A second ASGI
middleware guards everything except `/health` and `/metrics`. Verified live:

```
/v1/models    no key -> 401      /health   no key -> 200
/tokenize     no key -> 401      /metrics  no key -> 200
/invocations  no key -> 401      /invocations with key -> 200
```

**TLS and headers.** Caddy terminates Let's Encrypt TLS (HTTP/3 enabled) and sets HSTS
(`max-age=31536000; includeSubDomains`), `X-Content-Type-Options: nosniff`,
`Referrer-Policy: strict-origin-when-cross-origin`, and removes the `Server` header.
Port 80 exists only for the ACME challenge and the HTTPS redirect.

**Request correlation.** Every response carries `X-Request-ID`, echoed from the request when
supplied; the same id appears in the structured JSON log line. There is no PII in the logs —
filenames and card ids, not extracted field values.

### What is deliberately absent, and why

- **No authentication.** This is a demo whose purpose is that a reviewer can use it without
  credentials. That decision is stated rather than disguised, and it is
  [limitation #6](07-limitations.md).
- **No rate limiting.** Same reason. The honest mitigation for a real deployment is a per-IP
  token bucket in Caddy plus a daily batch cap; roughly half a day's work.
- **`CORS_ORIGINS=*`.** The developer stack is genuinely cross-origin (`:3000` → `:8000`).
  Behind the reverse proxy both are same-origin and this should be tightened to the deployed
  hostname. Note that `allow_credentials` is automatically disabled when the origin list is
  `*`, so a wildcard cannot be combined with cookie auth by accident.
- **No PII retention policy.** The largest gap, covered in detail as
  [limitation #8](07-limitations.md): no deletion endpoint, no TTL, no encryption at rest
  beyond the cloud disk. The demo is defensible only because the bundled corpus is entirely
  synthetic — invented people, `.example` domains that cannot resolve, and phone numbers from
  ranges regulators reserve for fiction.

### If a key leaks

1. `modal secret create ak-project-vlm-auth VLM_API_KEY='<new value>'` (overwrites).
2. Redeploy the endpoint so containers pick the new secret up.
3. Update `VLM_API_KEY` in the host's `.env` and restart the API service.

The old key stops working at step 2. There is no key rotation schedule and no second key for
zero-downtime rotation — a gap, not a feature.

---

## Cost

### Application host

On-demand list prices for `asia-south1`, pulled from the Cloud Billing Catalog API rather than
a pricing page, with no committed-use or sustained-use discount assumed — so these are upper
bounds.

| Resource | Spec | Hourly |
|---|---|---|
| vCPU | 8 × C3 core | $0.288288 |
| RAM | 32 GiB C3 | $0.131057 |
| Boot disk | 100 GiB Balanced PD | $0.016438 |
| Static IP | attached to a running VM | $0.005000 |
| Ubuntu 24.04 LTS | standard, not Pro | $0.000000 |
| VPC, subnet, 2 firewall rules | — | $0.000000 |
| **Total, running** | | **$0.4408 / hr** |

- **$0.44 / hour**
- **$10.58 / day**
- **$321.77 / 30-day month**

Egress is billed separately (~$0.12/GiB from Mumbai on Premium Tier) and is genuinely
negligible here — demo traffic is a few MB — so it is called out rather than silently folded
in.

This host runs **both** assignments; LeadForge's share is roughly half.

**Stopped**, the VM keeps only storage, and an unattached static IP is billed at a *higher*
rate than an attached one ($0.012/hr vs $0.005/hr — idle address reservations are priced
deliberately):

| | Hourly | Daily |
|---|---|---|
| 100 GiB Balanced PD | $0.016438 | $0.3945 |
| Reserved but unattached IP | $0.012000 | $0.2880 |
| **Total, stopped** | **$0.0284** | **$0.68** |

Stopping saves ~94 % while keeping the IP — and therefore the `nip.io` hostnames and the
already-issued certificates, so restarting touches no Let's Encrypt rate limit.

```bash
gcloud compute instances stop  ak-project-web --zone=asia-south1-b   # $10.58/day -> $0.68/day
gcloud compute instances start ak-project-web --zone=asia-south1-b   # comes back on its own
cd infra && ./teardown.sh --yes                                      # -> $0
```

### GPU

Modal charges per container-second while a container exists, and **nothing while scaled to
zero**.

```
L4          $0.000222 / s
CPU+memory  $0.000019 / s
total       $0.000241 / s  =  $0.87 per hour of container wall-clock
```

| Scenario | Container seconds | Cost |
|---|---|---|
| 25-card batch from cold | 210 boot + 47 infer + 900 idle tail | **$0.28** |
| 25-card batch, already warm | 47 infer (+ shared idle tail) | **$0.011** |
| Per card, warm | ~1.9 | $0.0005 |
| Idle overnight | 0 | **$0.00** |

The `scaledown_window` is the main lever, set to **900 s**. Cold start is model-load-bound
rather than compute-bound, so a larger GPU would not shorten it; only staying alive between
batches does. At 900 s a reviewer who uploads a second batch within 15 minutes pays no cold
start, and an abandoned tab costs ~$0.22 of idle instead of ~$0.06 — about $0.20 per session
to remove a 3½-minute wait from the demo. Weight download runs once on a **CPU** container
(~28 s, ~$0.0005) rather than burning L4 seconds on a 7 GB pull.

Building and benchmarking the endpoint — 9 cold starts, the throughput passes, the
eager-vs-graphs A/B and the image builds — cost **$0.83** of credit in total. Standing the same
deployment up cleanly costs **$0.07**: one weight download, one cold start, one extraction.

### What the shape of the bill says about the design

```
idle:      host $10.58/day  +  GPU $0.00/day
100 batches/day, warm:  host $10.58  +  GPU ~$1.10
```

**The GPU is not the expensive part; the always-on VM is.** That is the direct consequence of
scale-to-zero, and it is why the cold start was worth optimising and the pipeline was not: a
$0 idle GPU is what makes a demo affordable to leave running, and the price of it is
[limitation #1](07-limitations.md).

Full host breakdown and the exact stop/delete commands: [`../../infra/COST.md`](../../infra/COST.md).
