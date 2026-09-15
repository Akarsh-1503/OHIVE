#!/usr/bin/env bash
#
# teardown.sh — delete everything provision.sh created, in dependency order.
#
#   ./teardown.sh          # prompts first
#   ./teardown.sh --yes    # no prompt
#
# Only touches resources whose names start with ak-project-. It will never delete
# anything it did not create.

set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-asia-south1}"
ZONE="${ZONE:-asia-south1-b}"
NAME="${NAME:-ak-project-web}"

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

if [ "${1:-}" != "--yes" ]; then
  cat <<EOF
About to permanently delete, in project $PROJECT:
  instance        $NAME ($ZONE)          <- including its 100 GB boot disk
  address         ak-project-ip ($REGION)
  firewall        ak-project-allow-web, ak-project-allow-ssh-iap
  subnet          ak-project-subnet ($REGION)
  network         ak-project-vpc

Any data under /opt/ak-project on the VM goes with it.
EOF
  read -r -p "Type 'delete' to continue: " reply
  [ "$reply" = "delete" ] || { echo "aborted"; exit 1; }
fi

# Instance first: it holds the static IP and is targeted by the firewall rules.
log "deleting instance $NAME"
gcloud compute instances delete "$NAME" \
  --project="$PROJECT" --zone="$ZONE" --quiet 2>/dev/null || echo "  (already gone)"

log "releasing static IP ak-project-ip"
gcloud compute addresses delete ak-project-ip \
  --project="$PROJECT" --region="$REGION" --quiet 2>/dev/null || echo "  (already gone)"

for fw in ak-project-allow-web ak-project-allow-ssh-iap; do
  log "deleting firewall $fw"
  gcloud compute firewall-rules delete "$fw" \
    --project="$PROJECT" --quiet 2>/dev/null || echo "  (already gone)"
done

log "deleting subnet ak-project-subnet"
gcloud compute networks subnets delete ak-project-subnet \
  --project="$PROJECT" --region="$REGION" --quiet 2>/dev/null || echo "  (already gone)"

log "deleting network ak-project-vpc"
gcloud compute networks delete ak-project-vpc \
  --project="$PROJECT" --quiet 2>/dev/null || echo "  (already gone)"

log "done — remaining ak-project resources (should be empty):"
gcloud compute instances list --project="$PROJECT" --filter="name~^ak-project-" --format="value(name)"
gcloud compute addresses list --project="$PROJECT" --filter="name~^ak-project-" --format="value(name)"
