#!/usr/bin/env bash
#
# provision.sh — create the cloud footprint for the demo stack.
#
# Idempotent: every step checks for the resource first, so re-running is a no-op
# once everything exists. Safe to run after a partial failure.
#
# Creates:
#   - a dedicated VPC + subnet (isolation, see NOTE below)
#   - a regional static external IP
#   - two firewall rules (80/443 from anywhere; 22 from the IAP range only)
#   - one VM with the bootstrap script attached as its startup-script
#
# NOTE on the dedicated VPC: the project's `default` network already carries a
# blanket "allow all protocols from 0.0.0.0/0" rule that targets every instance
# in it. Joining that network would silently expose SSH and every container port
# to the internet, and narrowing that rule would break unrelated workloads. A
# separate VPC is the only way to get a correct ingress posture without touching
# anything that already exists.

set -euo pipefail

# ---------------------------------------------------------------- parameters
PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-asia-south1}"
# c3-standard-8 is not offered in asia-south1-a; -b and -c both have it.
ZONE="${ZONE:-asia-south1-b}"
NAME="${NAME:-ak-project-web}"

# Preference order. C3 (Sapphire Rapids) is the fastest per-core option here and
# matters because one of the apps publishes a CPU benchmark measured on this host.
# Fallbacks exist because C3 stock in asia-south1 is not guaranteed.
MACHINE_TYPES=("${MACHINE_TYPE:-c3-standard-8}" "c2-standard-8" "n2-standard-8")

NETWORK="ak-project-vpc"
SUBNET="ak-project-subnet"
SUBNET_CIDR="10.60.0.0/24"
ADDRESS_NAME="ak-project-ip"
TAG="ak-project-web"
BOOT_DISK_GB="100"
BOOT_DISK_TYPE="pd-balanced"
IMAGE_FAMILY="ubuntu-2404-lts-amd64"
IMAGE_PROJECT="ubuntu-os-cloud"
LABELS="purpose=ak-project-demo"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
have() { gcloud "$@" --project="$PROJECT" >/dev/null 2>&1; }

log "Project=$PROJECT Region=$REGION Zone=$ZONE Name=$NAME"

# ------------------------------------------------------------------- network
if have compute networks describe "$NETWORK"; then
  log "VPC $NETWORK already exists"
else
  log "Creating VPC $NETWORK"
  gcloud compute networks create "$NETWORK" \
    --project="$PROJECT" \
    --subnet-mode=custom \
    --bgp-routing-mode=regional
fi

if have compute networks subnets describe "$SUBNET" --region="$REGION"; then
  log "Subnet $SUBNET already exists"
else
  log "Creating subnet $SUBNET ($SUBNET_CIDR)"
  gcloud compute networks subnets create "$SUBNET" \
    --project="$PROJECT" \
    --network="$NETWORK" \
    --region="$REGION" \
    --range="$SUBNET_CIDR"
fi

# ------------------------------------------------------------------ firewall
# Public web ingress, scoped to the network tag so it only ever applies to this VM.
if have compute firewall-rules describe ak-project-allow-web; then
  log "Firewall ak-project-allow-web already exists"
else
  log "Creating firewall ak-project-allow-web (tcp:80,443 from 0.0.0.0/0)"
  gcloud compute firewall-rules create ak-project-allow-web \
    --project="$PROJECT" \
    --network="$NETWORK" \
    --direction=INGRESS \
    --action=ALLOW \
    --rules=tcp:80,tcp:443 \
    --source-ranges=0.0.0.0/0 \
    --target-tags="$TAG" \
    --description="Public HTTP/HTTPS to the demo reverse proxy"
fi

# SSH is deliberately NOT open to the internet. 35.235.240.0/20 is Google's
# Identity-Aware Proxy TCP-forwarding range, so `gcloud compute ssh
# --tunnel-through-iap` works while port 22 stays unreachable from the outside.
if have compute firewall-rules describe ak-project-allow-ssh-iap; then
  log "Firewall ak-project-allow-ssh-iap already exists"
else
  log "Creating firewall ak-project-allow-ssh-iap (tcp:22 from IAP range only)"
  gcloud compute firewall-rules create ak-project-allow-ssh-iap \
    --project="$PROJECT" \
    --network="$NETWORK" \
    --direction=INGRESS \
    --action=ALLOW \
    --rules=tcp:22 \
    --source-ranges=35.235.240.0/20 \
    --target-tags="$TAG" \
    --description="SSH via Identity-Aware Proxy only"
fi

# ----------------------------------------------------------------- static IP
if have compute addresses describe "$ADDRESS_NAME" --region="$REGION"; then
  log "Static IP $ADDRESS_NAME already exists"
else
  log "Reserving static IP $ADDRESS_NAME"
  gcloud compute addresses create "$ADDRESS_NAME" \
    --project="$PROJECT" \
    --region="$REGION"
fi

PUBLIC_IP="$(gcloud compute addresses describe "$ADDRESS_NAME" \
  --project="$PROJECT" --region="$REGION" --format='value(address)')"
log "Static IP = $PUBLIC_IP"

# ----------------------------------------------------------------------- VM
if have compute instances describe "$NAME" --zone="$ZONE"; then
  log "Instance $NAME already exists in $ZONE"
else
  # enable-oslogin=FALSE is deliberate: OS Login rejects operator accounts that
  # live outside the project's organisation, which is the case here. SSH instead
  # uses the project-level ssh-keys metadata, the mechanism this project already
  # relies on for its other hosts.
  created=""
  for mt in "${MACHINE_TYPES[@]}"; do
    log "Attempting to create $NAME as $mt in $ZONE"
    if gcloud compute instances create "$NAME" \
        --project="$PROJECT" \
        --zone="$ZONE" \
        --machine-type="$mt" \
        --image-family="$IMAGE_FAMILY" \
        --image-project="$IMAGE_PROJECT" \
        --boot-disk-size="${BOOT_DISK_GB}GB" \
        --boot-disk-type="$BOOT_DISK_TYPE" \
        --boot-disk-device-name="$NAME" \
        --network-interface="subnet=$SUBNET,address=$PUBLIC_IP" \
        --tags="$TAG" \
        --labels="$LABELS" \
        --no-deletion-protection \
        --metadata-from-file="startup-script=$SCRIPT_DIR/bootstrap.sh" \
        --metadata="enable-oslogin=FALSE" \
        --scopes=cloud-platform; then
      created="$mt"
      break
    fi
    log "$mt unavailable or out of quota, trying the next option"
  done
  [ -n "$created" ] || { echo "FATAL: no machine type could be provisioned" >&2; exit 1; }
  log "Created with machine type: $created"
fi

# ------------------------------------------------------------------ summary
gcloud compute instances describe "$NAME" --project="$PROJECT" --zone="$ZONE" \
  --format='table(name,zone.basename(),machineType.basename(),status,
                  networkInterfaces[0].accessConfigs[0].natIP:label=EXTERNAL_IP)'

DASHED="${PUBLIC_IP//./-}"
cat <<EOF

Provisioned. Public hostnames (nip.io resolves these to $PUBLIC_IP automatically):
  https://leads-${DASHED}.nip.io
  https://slam-${DASHED}.nip.io

SSH:
  gcloud compute ssh $NAME --zone=$ZONE --project=$PROJECT --tunnel-through-iap

Next: ./deploy.sh
EOF
