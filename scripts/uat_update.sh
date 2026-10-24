#!/usr/bin/env bash
# Update the Kanak UAT backend on the EC2 box (run ON the server as ubuntu).
#
#   bash scripts/uat_update.sh            # pull main, migrate, collectstatic, restart
#   BRANCH=feature/x bash scripts/uat_update.sh
#
# First run migrates the old monorepo checkout (~/apps/kanak-beautyy/backend)
# to this repo at ~/apps/kanak-beauty-backend, reusing its .env and media,
# and repoints the systemd unit + nginx site. Safe to re-run.
set -euo pipefail

REPO="${REPO:-https://github.com/Dkyadav29/kanak-beauty-backend.git}"
BRANCH="${BRANCH:-main}"
APP="${APP:-$HOME/apps/kanak-beauty-backend}"
OLD="$HOME/apps/kanak-beautyy/backend"
UNIT=/etc/systemd/system/kanak-backend.service
SITE=/etc/nginx/sites-available/kanak

log() { printf '\n==> %s\n' "$*"; }

if [ ! -d "$APP/.git" ]; then
  log "Cloning $REPO into $APP"
  git clone --branch "$BRANCH" "$REPO" "$APP"
  if [ -d "$OLD" ]; then
    log "Migrating .env / media from old checkout $OLD"
    [ -f "$OLD/.env" ] && cp -n "$OLD/.env" "$APP/.env"
    [ -d "$OLD/media" ] && [ ! -d "$APP/media" ] && mv "$OLD/media" "$APP/media"
  fi
fi

cd "$APP"
log "Fetching $BRANCH"
git fetch origin "$BRANCH"
git checkout -q "$BRANCH"
git reset -q --hard "origin/$BRANCH"
git log --oneline -1

[ -f .env ] || { echo "ERROR: $APP/.env missing. Copy backend/.env.example and fill it in."; exit 1; }
# A venv moved from another path has stale shebangs; rebuild it if pip won't run.
.venv/bin/pip --version >/dev/null 2>&1 || { log "Creating venv"; rm -rf .venv; python3 -m venv .venv; }

log "Installing requirements"
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

set -a; . ./.env; set +a
log "Migrating database"
.venv/bin/python manage.py migrate --noinput
log "Collecting static files"
.venv/bin/python manage.py collectstatic --noinput | tail -1
mkdir -p media

if grep -q "$OLD" "$UNIT" 2>/dev/null || grep -q "$OLD" "$SITE" 2>/dev/null; then
  log "Repointing systemd unit and nginx site to $APP"
  sudo sed -i "s|$OLD|$APP|g" "$UNIT" "$SITE"
  sudo systemctl daemon-reload
  sudo nginx -t && sudo systemctl reload nginx
fi

log "Restarting kanak-backend"
sudo systemctl restart kanak-backend
sleep 3
systemctl is-active kanak-backend
HOST="$(grep -E '^DJANGO_ALLOWED_HOSTS=' .env | cut -d= -f2 | cut -d, -f1)"
curl -fsS -H "Host: ${HOST:-127.0.0.1}" http://127.0.0.1/api/v1/health && echo
log "Done."
