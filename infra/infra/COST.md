# Cost

All figures are **on-demand list prices for `asia-south1` (Mumbai)**, pulled from the Cloud
Billing Catalog API (`cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus`) on
2026-09-14, not from a pricing page. No committed-use or sustained-use discount is assumed,
so these are upper bounds.

## What is provisioned

| Resource | Spec | SKU rate | Hourly |
|---|---|---|---|
| `ak-project-web` vCPU | 8 × C3 core | $0.036036 / vCPU-hr | $0.288288 |
| `ak-project-web` RAM | 32 GiB C3 | $0.00409552 / GiB-hr | $0.131057 |
| Boot disk | 100 GiB Balanced PD | $0.12 / GiB-month ÷ 730 | $0.016438 |
| `ak-project-ip` | external IP, attached to a running VM | $0.005 / hr | $0.005000 |
| Ubuntu 24.04 LTS | standard, not Pro | no licence fee | $0.000000 |
| VPC, subnet, 2 firewall rules | — | free | $0.000000 |
| **Total, running** | | | **$0.4408 / hr** |

- **Per hour: $0.44**
- **Per day: $10.58**
- **Per 30-day month (730 h): $321.77**

Network egress is billed separately (~$0.12/GiB from Mumbai to the internet on Premium
Tier) and is **not** included above. It is genuinely negligible here — demo traffic is a
few MB — but a large PLY point-cloud export would show up, so it is called out rather
than silently folded in.

## Cost while stopped

Stopping the VM releases the CPU and RAM charge but not storage, and an unattached static
IP is billed at a *higher* rate than an attached one ($0.012/hr vs $0.005/hr) — Google
prices idle address reservations deliberately.

| Resource | Hourly | Daily |
|---|---|---|
| 100 GiB Balanced PD | $0.016438 | $0.3945 |
| `ak-project-ip`, reserved but unattached | $0.012000 | $0.2880 |
| **Total, stopped** | **$0.0284 / hr** | **$0.68 / day** |

So stopping saves ~94% while keeping the IP (and therefore the `nip.io` hostnames and the
issued certificates) reserved.

## Stop it (keeps the disk, the IP and the certs)

```bash
gcloud compute instances stop ak-project-web --zone=asia-south1-b
```

Restart later with `gcloud compute instances start ak-project-web --zone=asia-south1-b`.
The stack comes back on its own: `ak-project-stack.service` is enabled, and the Caddy
certificate store is a bind mount on the boot disk, so nothing is re-issued and no
Let's Encrypt rate limit is touched.

## Delete it (stops all charges)

```bash
./teardown.sh --yes
```

Which is equivalent to, in this order:

```bash
gcloud compute instances delete ak-project-web --zone=asia-south1-b --quiet
gcloud compute addresses delete ak-project-ip --region=asia-south1 --quiet
gcloud compute firewall-rules delete ak-project-allow-web --quiet
gcloud compute firewall-rules delete ak-project-allow-ssh-iap --quiet
gcloud compute networks subnets delete ak-project-subnet --region=asia-south1 --quiet
gcloud compute networks delete ak-project-vpc --quiet
```

Deleting the instance deletes its boot disk too — `--boot-disk-auto-delete` is the default
and was not overridden. Deletion protection is off, so no extra flag is needed.
