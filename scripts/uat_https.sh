#!/usr/bin/env bash
# Put the Kanak UAT backend behind HTTPS (Let's Encrypt) so an https:// frontend
# (e.g. Cloudflare) can call it without "blocked:mixed-content". Run ON the server
# as ubuntu, after opening port 443 in the EC2 security group.
#
#   FRONTEND_ORIGIN=https://kanak-beauty.xxx.workers.dev bash scripts/uat_https.sh
#   DOMAIN=api.example.com EMAIL=you@example.com FRONTEND_ORIGIN=https://... bash scripts/uat_https.sh
#
# DOMAIN defaults to the free sslip.io name for this box's IP. Safe to re-run.
set -euo pipefail

DOMAIN="${DOMAIN:-18-119-106-180.sslip.io}"
FRONTEND_ORIGIN="${FRONTEND_ORIGIN:?Set FRONTEND_ORIGIN to the Cloudflare site URL, e.g. https://kanak-beauty.xxx.workers.dev}"
FRONTEND_ORIGIN="${FRONTEND_ORIGIN%/}"
EMAIL="${EMAIL:-}"
APP="${APP:-$HOME/apps/kanak-beauty-backend}"
SITE=/etc/nginx/sites-available/kanak

log() { printf '\n==> %s\n' "$*"; }

# Append VALUE to the comma-separated KEY in .env (adds the key if missing).
add_csv() {
  local key="$1" value="$2" current
  current="$(grep -E "^${key}=" .env | tail -1 | cut -d= -f2- || true)"
  if [ -z "$current" ]; then
    sed -i "/^${key}=/d" .env
    echo "${key}=${value}" >> .env
  elif ! printf '%s' ",$current," | grep -qF ",$value,"; then
    sed -i "s|^${key}=.*|${key}=${current},${value}|" .env
  fi
}

set_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" .env; then sed -i "s|^${key}=.*|${key}=${value}|" .env; else echo "${key}=${value}" >> .env; fi
}

log "Installing certbot"
sudo apt-get update -qq
sudo apt-get install -y -qq certbot python3-certbot-nginx

log "Adding $DOMAIN to nginx server_name"
sudo cp "$SITE" "$SITE.bak.$(date +%Y%m%d-%H%M%S)"
if ! sudo grep -qE "server_name[^;]*\b${DOMAIN//./\\.}\b" "$SITE"; then
  if sudo grep -qE '^\s*server_name\s' "$SITE"; then
    sudo sed -i -E "0,/^(\s*server_name)\s/s//\1 ${DOMAIN} /" "$SITE"
  else
    sudo sed -i -E "0,/^(\s*)listen\s+80[^;]*;/s//&\n\1server_name ${DOMAIN};/" "$SITE"
  fi
fi
if ! sudo nginx -t; then
  echo "nginx config invalid; restoring backup" >&2
  sudo cp "$(ls -t "$SITE".bak.* | head -1)" "$SITE"
  exit 1
fi
sudo systemctl reload nginx

log "Requesting certificate for $DOMAIN"
if [ -n "$EMAIL" ]; then MAIL_ARGS=(--email "$EMAIL"); else MAIL_ARGS=(--register-unsafely-without-email); fi
# --no-redirect keeps http://18.119.106.180 working for anything still using it.
sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --no-redirect "${MAIL_ARGS[@]}"

log "Updating backend .env"
cd "$APP"
cp .env ".env.bak.$(date +%Y%m%d-%H%M%S)"
add_csv DJANGO_ALLOWED_HOSTS "$DOMAIN"
add_csv CORS_ALLOWED_ORIGINS "$FRONTEND_ORIGIN"
add_csv CSRF_TRUSTED_ORIGINS "$FRONTEND_ORIGIN"
add_csv CSRF_TRUSTED_ORIGINS "https://$DOMAIN"
set_env PUBLIC_SITE_URL "$FRONTEND_ORIGIN"
grep -E '^(DJANGO_ALLOWED_HOSTS|CORS_ALLOWED_ORIGINS|CSRF_TRUSTED_ORIGINS|PUBLIC_SITE_URL)=' .env

log "Restarting kanak-backend"
sudo systemctl restart kanak-backend
sleep 3
systemctl is-active kanak-backend

log "Checking https://$DOMAIN"
curl -fsS "https://$DOMAIN/api/v1/health" && echo
if curl -fsS -o /dev/null -D - -H "Origin: $FRONTEND_ORIGIN" "https://$DOMAIN/api/v1/academy/stats" \
  | grep -qi "^access-control-allow-origin: $FRONTEND_ORIGIN"; then
  echo "CORS OK for $FRONTEND_ORIGIN"
else
  echo "WARNING: no CORS header for $FRONTEND_ORIGIN; check CORS_ALLOWED_ORIGINS in $APP/.env" >&2
fi

log "Done. Frontend API URL: https://$DOMAIN/api/v1 (certbot renews automatically)"
