#!/usr/bin/env bash
#
# deploy.sh — one-command deploy. Run it from a laptop; it needs nothing on that
# laptop except gcloud and tar.
#
#   ./deploy.sh              # sync + build + restart + smoke test
#   ./deploy.sh --no-build   # ship config only (e.g. a Caddyfile tweak)
#
# Strategy: source is streamed to the VM over the IAP SSH tunnel as a tarball and
# the images are built ON the VM. No container registry, no auth plumbing, no
# cross-architecture headaches (an Apple Silicon laptop would otherwise produce
# arm64 images this amd64 host cannot run).
#
# Build contexts are skipped when they contain no Dockerfile, so this script is
# runnable before the applications exist — it will simply deploy the proxy.

set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
ZONE="${ZONE:-asia-south1-b}"
NAME="${NAME:-ak-project-web}"
STACK_DIR=/opt/ak-project

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"

BUILD=1
[ "${1:-}" = "--no-build" ] && BUILD=0

# service name : local source dir : remote subdir : compose profile
#   [ : build context (under $STACK_DIR) : Dockerfile relative to that context ]
#
# The last two fields default to "the remote subdir" and "Dockerfile". slam-api overrides
# them because its image installs the sibling `slam` package and therefore has to be built
# one directory up, with assignment2/ as its context.
TARGETS=(
  "leads-api:$REPO_ROOT/assignment1/backend:src/leads/backend:leads"
  "leads-web:$REPO_ROOT/assignment1/frontend:src/leads/frontend:leads"
  "slam-api:$REPO_ROOT/assignment2/backend:src/slam/backend:slam:src/slam:backend/Dockerfile"
  "slam-web:$REPO_ROOT/assignment2/frontend:src/slam/frontend:slam"
)

# Source trees that are not an image of their own but belong to somebody else's build
# context, plus the .dockerignore that keeps that context small. local path : remote path.
EXTRA_PATHS=(
  "$REPO_ROOT/assignment2/slam:src/slam/slam"
  "$REPO_ROOT/assignment2/samples:src/slam/samples"
  "$REPO_ROOT/assignment2/.dockerignore:src/slam/.dockerignore"
)

SSH=(gcloud compute ssh "$NAME" --zone="$ZONE" --project="$PROJECT" --tunnel-through-iap --quiet)

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

remote() { "${SSH[@]}" --command="$1"; }

# Stream a directory to the VM. Excludes everything that is either huge or
# rebuildable, which is what keeps this fast enough to beat a registry push.
push_dir() {
  local src="$1" dest="$2"
  [ -d "$src" ] || { log "skip $src (absent)"; return 0; }
  log "syncing $(basename "$(dirname "$src")")/$(basename "$src") -> $dest"
  remote "sudo mkdir -p '$STACK_DIR/$dest' && sudo chown -R \$(id -u):\$(id -g) '$STACK_DIR/$dest'"
  # COPYFILE_DISABLE stops BSD tar emitting AppleDouble "._name" side-files for
  # every macOS extended attribute. They are not just noise: `next build` lints
  # them, fails to parse them as TypeScript, and the image build dies. The
  # exclude and the remote purge cover copies made before this was fixed, since
  # extraction merges into the existing tree rather than replacing it.
  COPYFILE_DISABLE=1 tar --no-xattrs -czf - -C "$src" \
    --exclude='node_modules' \
    --exclude='.next' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --exclude='*.pyc' \
    --exclude='._*' \
    --exclude='.DS_Store' \
    --exclude='.env' \
    --exclude='.env.local' \
    --exclude='data' \
    . | remote "tar -xzf - -C '$STACK_DIR/$dest' \
        && find '$STACK_DIR/$dest' \( -name '._*' -o -name '.DS_Store' \) -delete"
}

# Single file, for the handful of dotfiles a build context needs but no directory owns.
# The remote name is taken from the source, so src and dest basenames must match.
push_file() {
  local src="$1" destdir
  destdir="$STACK_DIR/$(dirname "$2")"
  [ -f "$src" ] || { log "skip $src (absent)"; return 0; }
  log "syncing $(basename "$src") -> $2"
  remote "sudo mkdir -p '$destdir' && sudo chown -R \$(id -u):\$(id -g) '$destdir'"
  COPYFILE_DISABLE=1 tar --no-xattrs -czf - -C "$(dirname "$src")" "$(basename "$src")" \
    | remote "tar -xzf - -C '$destdir'"
}

push_path() {
  if [ -d "$1" ]; then push_dir "$1" "$2"; else push_file "$1" "$2"; fi
}

# ------------------------------------------------------------ 1. infra config
log "shipping stack config"
# Deliberately NOT `chown -R` on the whole stack dir: that would also take
# ownership of data/, whose files are written by the application containers
# under their own non-root uids. Doing so leaves them with a read-only database
# on the next deploy ("attempt to write a readonly database"). data/ is runtime
# state, not deploy state, so deploy never touches its ownership.
remote "sudo mkdir -p '$STACK_DIR/src' '$STACK_DIR/data' \
  && sudo chown \$(id -u):\$(id -g) '$STACK_DIR' \
  && sudo chown -R \$(id -u):\$(id -g) '$STACK_DIR/src'"
tar --no-xattrs -czf - -C "$INFRA_DIR" \
  docker-compose.yml docker-compose.placeholder.yml Caddyfile bootstrap.sh \
  | remote "tar -xzf - -C '$STACK_DIR'"

# bootstrap is idempotent; re-running it here picks up any change to the systemd
# unit or the derived .env without needing a reboot.
remote "sudo bash '$STACK_DIR/bootstrap.sh' >/dev/null && echo 'bootstrap ok'"

# --------------------------------------------------------------- 2. app source
for t in "${TARGETS[@]}"; do
  IFS=: read -r _svc src dest _grp <<<"$t"
  push_dir "$src" "$dest"
done
for p in "${EXTRA_PATHS[@]}"; do
  IFS=: read -r src dest <<<"$p"
  push_path "$src" "$dest"
done

# ------------------------------------------------------------------ 3. build
if [ "$BUILD" -eq 1 ]; then
  for t in "${TARGETS[@]}"; do
    IFS=: read -r svc _src dest _grp ctx dockerfile <<<"$t"
    ctx="${ctx:-$dest}"
    dockerfile="${dockerfile:-Dockerfile}"
    if remote "test -f '$STACK_DIR/$ctx/$dockerfile'"; then
      log "building ak-project/$svc:latest from $ctx (-f $dockerfile)"
      # A broken build must not abort the deploy: the profile gate below simply
      # leaves that application switched off, and the other one still ships.
      # Without this, one unfinished assignment takes the whole box down with it.
      remote "cd '$STACK_DIR/$ctx' && docker build --pull -f '$dockerfile' -t 'ak-project/$svc:latest' ." \
        || log "BUILD FAILED for ak-project/$svc — its profile will stay off"
    else
      log "no $dockerfile in $ctx — skipping ak-project/$svc (app not written yet)"
    fi
  done
else
  log "--no-build: skipping image builds"
fi

# ------------------------------------------------------------- 4. restart
# A profile is switched on only once every image it needs genuinely exists, so a
# reboot never leaves the box without TLS waiting on an image that was never built.
#
# The profiles are per application rather than one shared `apps`: with a single
# gate, one unfinished assignment keeps the *other*, finished one offline too.
# Compose still understands `apps` as "everything", because every service lists
# both its own profile and `apps`.
log "checking which application images exist"
# The trailing `true` matters: the loop's exit status is that of the last
# `docker image inspect`, and under `set -e` a failing command substitution would
# abort the deploy exactly when an image is missing — the case this must handle.
PRESENT="$(remote 'for i in leads-api leads-web slam-api slam-web; do
  docker image inspect "ak-project/$i:latest" >/dev/null 2>&1 && echo "$i"
done; true' | tr -d "\r")"

PROFILES=()
for grp in $(printf '%s\n' "${TARGETS[@]}" | cut -d: -f4 | awk '!seen[$0]++'); do
  missing=""
  for t in "${TARGETS[@]}"; do
    # The trailing _rest matters: without it `g` would swallow the optional build-context
    # fields and no target would ever match its own profile.
    IFS=: read -r svc _src _dest g _rest <<<"$t"
    [ "$g" = "$grp" ] || continue
    grep -qx "$svc" <<<"$PRESENT" || missing="$missing $svc"
  done
  if [ -z "$missing" ]; then
    PROFILES+=("$grp")
  else
    log "profile '$grp' stays off — no image for:$missing"
  fi
done

JOINED="$(IFS=,; echo "${PROFILES[*]}")"
if [ -n "$JOINED" ]; then
  log "enabling profiles: $JOINED"
else
  log "no complete application found — starting the proxy only"
fi
remote "sed -i 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=$JOINED/' '$STACK_DIR/.env'"

log "restarting ak-project-stack.service"
remote "sudo systemctl restart ak-project-stack.service && sudo systemctl --no-pager --lines=0 status ak-project-stack.service | head -5"
remote "cd '$STACK_DIR' && docker compose ps"

# --------------------------------------------------------------- 5. smoke test
PUBLIC_IP="$(gcloud compute addresses describe ak-project-ip \
  --project="$PROJECT" --region="${ZONE%-*}" --format='value(address)')"
DASHED="${PUBLIC_IP//./-}"

log "smoke testing public endpoints"
for host in "leads-${DASHED}.nip.io" "slam-${DASHED}.nip.io"; do
  printf '  %-34s ' "$host"
  # Caddy issues on first request, so allow a generous window on a cold deploy.
  curl -sS -o /dev/null -w 'HTTP %{http_code}  TLS %{ssl_verify_result}(0=valid)\n' \
    --max-time 90 "https://$host/" || echo "FAILED"
done

cat <<EOF

Deployed.
  https://leads-${DASHED}.nip.io
  https://slam-${DASHED}.nip.io

Logs:  gcloud compute ssh $NAME --zone=$ZONE --tunnel-through-iap --command='cd $STACK_DIR && docker compose logs -f'
EOF
