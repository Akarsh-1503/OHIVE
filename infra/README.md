# Infrastructure — deployment runbook

One Compute Engine VM running both applications behind a single Caddy reverse proxy with
automatic Let's Encrypt TLS. Everything here is reproducible from this directory; there is
no console click-ops step.

## Live

| | |
|---|---|
| Instance | `ak-project-web`, `asia-south1-b`, `c3-standard-8` |
| CPU | Intel Xeon Platinum 8481C @ 2.70 GHz (Sapphire Rapids) — 8 vCPU / 4 cores × 2 threads, AVX-512 + AMX |
| RAM / disk | 32 GiB / 100 GiB Balanced PD |
| OS | Ubuntu 24.04 LTS (Noble) |
| Static IP | `ak-project-ip` → `34.47.153.95` |
| Assignment 1 | https://leads-34-47-153-95.nip.io |
| Assignment 2 | https://slam-34-47-153-95.nip.io |

Hostnames come from [nip.io](https://nip.io), which resolves `<anything>-1-2-3-4.nip.io`
to `1.2.3.4`. It is on the Public Suffix List, so Let's Encrypt treats `leads-…nip.io` and
`slam-…nip.io` as separate registrable domains — each gets its own certificate and its own
rate-limit budget. No domain registration, no DNS zone to manage.

## Prerequisites

- `gcloud` installed and authenticated (`gcloud auth login`)
- A GCP project with billing enabled, and `gcloud config set project <id>`
- Compute Engine and IAP APIs enabled
- `tar` and `curl` locally. That is the whole list — no Terraform, no Docker on the laptop,
  no container registry.

## Provision

```bash
cd infra
./provision.sh
```

Creates, in order: a dedicated VPC + subnet, two firewall rules, a regional static IP, and
the VM with `bootstrap.sh` attached as its startup-script. Every step is guarded by an
existence check, so re-running it is a no-op and a partial failure can simply be re-run.

Override any of these from the environment:

```bash
PROJECT=my-proj ZONE=asia-south1-c NAME=ak-project-web ./provision.sh
```

`provision.sh` tries `c3-standard-8` first and falls back to `c2-standard-8` then
`n2-standard-8` if C3 stock or quota is unavailable, reporting what it actually got.

### Networking decisions

- **A dedicated VPC (`ak-project-vpc`), not `default`.** The project's `default` network
  already has a blanket *allow-all-protocols from `0.0.0.0/0`* rule with no target tags.
  Any VM placed there inherits it, which would expose SSH and every container port.
  Narrowing that rule would break unrelated workloads, so the VM gets its own network.
- **Port 22 is not open to the internet.** `ak-project-allow-ssh-iap` permits tcp:22 only
  from `35.235.240.0/20`, Google's Identity-Aware Proxy range. SSH therefore works through
  `--tunnel-through-iap` and nowhere else.
- **`ak-project-allow-web`** opens tcp:80 and tcp:443 to `0.0.0.0/0`, scoped to the
  `ak-project-web` network tag. Port 80 is needed for the ACME HTTP-01 challenge and for
  the HTTP→HTTPS redirect.

## Deploy

```bash
cd infra
./deploy.sh
```

One command. It:

1. streams `docker-compose.yml`, `Caddyfile` and `bootstrap.sh` to `/opt/ak-project`
2. re-runs `bootstrap.sh` (idempotent) to refresh the systemd unit and the derived `.env`
3. streams each app's source to the VM over the IAP tunnel as a tarball
4. **builds the images on the VM**, not locally
5. restarts `ak-project-stack.service` and smoke-tests both public URLs

`./deploy.sh --no-build` ships configuration only — useful for a `Caddyfile` edit.

Images are built on the VM rather than pushed to Artifact Registry for two reasons: it
removes registry auth and lifecycle management entirely, and it avoids the architecture
mismatch an Apple Silicon laptop would otherwise produce (arm64 images this amd64 host
cannot run). The trade-off is that build time is spent on the VM and there is no image
history to roll back to — acceptable for a demo host, not for production.

### Where app source lives on the VM

```
/opt/ak-project/
├── docker-compose.yml              # the stack (shipped by deploy.sh)
├── docker-compose.placeholder.yml  # throwaway upstreams for smoke-testing the proxy
├── Caddyfile                       # reverse proxy + TLS
├── bootstrap.sh
├── .env                            # generated each boot; PUBLIC_IP, hostnames, profiles
├── caddy/data/                     # issued certificates — bind mount, survives recreation
├── caddy/config/
├── data/leads/                     # Assignment 1 DATA_DIR  -> container /data
├── data/slam/                      # Assignment 2 DATA_DIR  -> container /data
└── src/
    ├── leads/backend/              # <- assignment1/backend   => ak-project/leads-api
    ├── leads/frontend/             # <- assignment1/frontend  => ak-project/leads-web
    ├── slam/backend/               # <- assignment2/backend   => ak-project/slam-api
    └── slam/frontend/              # <- assignment2/frontend  => ak-project/slam-web
```

Each assignment owns its own `Dockerfile` at the root of its backend/frontend directory.
The top-level `docker-compose.yml` only consumes the resulting `ak-project/*:latest`
images; it never defines how they are built.

### The `apps` compose profile

The four application services carry `profiles: [apps]`. With `COMPOSE_PROFILES` empty,
`docker compose up -d` starts **only Caddy**. `deploy.sh` sets `COMPOSE_PROFILES=apps` in
`.env` once every image has actually been built.

This is what makes the box reboot-safe before the apps exist: systemd can bring TLS up on
boot without wedging on a missing image. Until then the sites return a plain-text 502 from
Caddy's `handle_errors` block over a valid certificate, which is the correct behaviour.

## SSH

```bash
gcloud compute ssh ak-project-web \
  --zone=asia-south1-b --tunnel-through-iap
```

(The project is taken from `gcloud config get-value project`; pass `--project=<id>`
explicitly if you work across several.)

`--tunnel-through-iap` is required; port 22 is not reachable from the internet.

> OS Login is explicitly disabled on this instance (`enable-oslogin=FALSE`). OS Login
> rejects operator accounts that live outside the project's organisation, which is the case
> for the account used here; SSH falls back to project-level `ssh-keys` metadata instead.

## Logs

```bash
S="gcloud compute ssh ak-project-web --zone=asia-south1-b --tunnel-through-iap --command"

# everything, following
$S 'cd /opt/ak-project && docker compose logs -f'

# one service
$S 'cd /opt/ak-project && docker compose logs -f --tail=200 caddy'
$S 'cd /opt/ak-project && docker compose logs -f --tail=200 slam-api'

# TLS / ACME issuance specifically
$S 'cd /opt/ak-project && docker compose logs caddy | grep -i "certificate\|acme\|tls"'

# the boot-time unit and the startup script
$S 'systemctl status ak-project-stack.service'
$S 'sudo journalctl -u google-startup-scripts --no-pager | tail -50'

# container health and resource use
$S 'docker compose -f /opt/ak-project/docker-compose.yml ps'
$S 'docker stats --no-stream'
```

## Verify

```bash
gcloud compute instances list --filter="name=ak-project-web"
curl -sSI https://leads-34-47-153-95.nip.io
curl -sSI https://slam-34-47-153-95.nip.io
```

To prove the public path without the real apps, bring up throwaway upstreams that take the
same network aliases the real services use — this exercises the live `Caddyfile`
unmodified:

```bash
S="gcloud compute ssh ak-project-web --zone=asia-south1-b --tunnel-through-iap --command"
$S 'cd /opt/ak-project && docker compose -f docker-compose.placeholder.yml up -d'
curl -sSI https://leads-34-47-153-95.nip.io      # -> 200
$S 'cd /opt/ak-project && docker compose -f docker-compose.placeholder.yml down'
```

## Tear down

```bash
cd infra
./teardown.sh          # prompts for confirmation
./teardown.sh --yes    # unattended
```

Deletes only resources named `ak-project-*`, in dependency order, and is safe to re-run.
To pause spending without destroying anything, see `COST.md` — stopping the VM keeps the
IP, the hostnames and the issued certificates for ~$0.68/day.

## Cost

$0.44/hour, $10.58/day running. Full breakdown and the stop/delete commands are in
[`COST.md`](./COST.md).

---

## Note on cloud provider: this is GCP, the brief says AWS

The brief targets AWS; this is deployed on **GCP Compute Engine**, because that is the
account with billing, quota and an existing footprint available to the author. Nothing in
the design is GCP-specific — the entire runtime is a `docker-compose.yml` and a `Caddyfile`
on one Linux box, which is deliberately the most portable shape this could take. The port
is a rewrite of `provision.sh`, not of the application.

### Direct equivalents

| This deployment (GCP) | AWS equivalent |
|---|---|
| `c3-standard-8` — 8 vCPU / 32 GiB, Xeon Platinum 8481C (Sapphire Rapids) | **`c7i.2xlarge`** — 8 vCPU / 16 GiB, Xeon Platinum 8488C (same Sapphire Rapids generation). For a like-for-like 32 GiB, **`m7i.2xlarge`** is the exact match; `c7i.2xlarge` matches the CPU but halves the RAM. |
| `ak-project-ip` static regional external IP | **Elastic IP**, associated with the instance |
| `ak-project-allow-web` (tcp:80,443 from `0.0.0.0/0`, scoped by network tag) | **Security group** inbound rules on 80/443 from `0.0.0.0/0`. Network tags → SG membership. |
| `ak-project-allow-ssh-iap` (tcp:22 from the IAP range only) | **No inbound 22 at all**; use **SSM Session Manager** (`aws ssm start-session --target i-…`). Same posture: no public SSH port. |
| `ak-project-vpc` + `ak-project-subnet` | **VPC** + public subnet + internet gateway + route table |
| Instance `startup-script` metadata → `bootstrap.sh` | **EC2 user-data**, byte-for-byte the same script |
| 100 GiB `pd-balanced` boot disk | **100 GiB `gp3` EBS** volume |
| `labels=purpose=ak-project-demo` | **Tags** — `Purpose=ak-project-demo` |
| `gcloud compute ssh --tunnel-through-iap` | `aws ssm start-session` |
| Ubuntu 24.04 LTS image family | Canonical Ubuntu 24.04 LTS AMI |

`docker-compose.yml`, `Caddyfile`, `bootstrap.sh` and the `ak-project-stack.service` unit
transfer **unchanged**. Only `provision.sh` and `teardown.sh` are rewritten, roughly
one-for-one against `aws ec2 …` / `aws ec2 allocate-address` / `aws ec2 authorize-security-group-ingress`.

### If you would rather not run a VM at all

The three stateless services (`leads-web`, `slam-web`, `leads-api`) map cleanly onto
**ECS Fargate** behind an **Application Load Balancer**, with TLS from **ACM** instead of
Let's Encrypt — at which point Caddy's only remaining job is path routing, and the ALB
listener rules (`/api/*` → the API target group, `/*` → the web target group) replace it
outright.

`slam-api` is the exception and is the reason this is one VM today: reconstruction is a
long-running, CPU-bound, stateful job that writes intermediate artefacts to `DATA_DIR` and
streams progress over SSE for minutes at a time. On Fargate that needs a 8 vCPU / 32 GiB
task definition, an **EFS** mount for `DATA_DIR`, and an ALB idle timeout raised well past
the 60 s default. It works, but it is strictly more moving parts than a single box for a
demo with one concurrent user — so the honest recommendation is: EC2 now, Fargate for the
web tier when traffic justifies splitting it.
