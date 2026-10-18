#!/usr/bin/env bash
# Wipe ALL academy data rows (keeps schema). Run from anywhere on the host.
#
#   scripts/purge_db.sh --dry-run                 # show row counts, delete nothing
#   scripts/purge_db.sh --yes                     # backup, then delete EVERYTHING (users too)
#   scripts/purge_db.sh --yes --keep-admins       # backup, then delete everything except admin users
#   scripts/purge_db.sh --yes --with-media        # also delete uploaded files in backend/media
#   scripts/purge_db.sh --yes --no-backup         # skip the mysqldump backup
#
# Fresh admin after a full wipe: set both variables and the script creates it.
#   NEW_ADMIN_EMAIL=you@example.com NEW_ADMIN_PASSWORD='Strong#Pass1' NEW_ADMIN_NAME='Academy Admin' \
#     scripts/purge_db.sh --yes --with-media
set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"; [ -x "$PY" ] || PY=".venv/Scripts/python.exe"; [ -x "$PY" ] || PY="python"
if [ -f .env ]; then set -a; . ./.env; set +a; fi

DO_BACKUP=1
WITH_MEDIA=0
DRY_RUN=0
CONFIRMED=0
PURGE_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --no-backup)  DO_BACKUP=0 ;;
    --with-media) WITH_MEDIA=1 ;;
    --dry-run)    DRY_RUN=1; PURGE_ARGS+=("$arg") ;;
    --yes)        CONFIRMED=1; PURGE_ARGS+=("$arg") ;;
    *)            PURGE_ARGS+=("$arg") ;;
  esac
done

# Preview only.
if [ "$DRY_RUN" -eq 1 ] || [ "$CONFIRMED" -eq 0 ]; then
  exec "$PY" manage.py purge_data "${PURGE_ARGS[@]}"
fi

# 1. Backup, so a wrong run can be restored with:  mysql -u USER -p DB_NAME < backups/<file>.sql
if [ "$DO_BACKUP" -eq 1 ]; then
  if ! command -v mysqldump >/dev/null 2>&1; then
    echo "mysqldump not found. Install it or rerun with --no-backup." >&2
    exit 1
  fi
  mkdir -p backups
  BACKUP="backups/${DB_NAME:-kanak_academy}-$(date +%Y%m%d-%H%M%S).sql"
  echo "Backing up ${DB_NAME:-kanak_academy} to $BACKUP"
  MYSQL_PWD="${DB_PASSWORD:-}" mysqldump --single-transaction --routines \
    -h "${DB_HOST:-127.0.0.1}" -P "${DB_PORT:-3306}" -u "${DB_USER:-kanak}" \
    "${DB_NAME:-kanak_academy}" > "$BACKUP"
fi

# 2. Delete all rows (one transaction: all or nothing).
"$PY" manage.py purge_data "${PURGE_ARGS[@]}"

# 3. Uploaded files (course thumbnails, recordings, materials).
if [ "$WITH_MEDIA" -eq 1 ] && [ -d media ]; then
  echo "Deleting uploaded files in $(pwd)/media"
  find media -mindepth 1 -delete
fi

# 4. Optional fresh admin.
if [ -n "${NEW_ADMIN_EMAIL:-}" ] && [ -n "${NEW_ADMIN_PASSWORD:-}" ]; then
  "$PY" manage.py create_admin --email "$NEW_ADMIN_EMAIL" --name "${NEW_ADMIN_NAME:-Academy Admin}" --password "$NEW_ADMIN_PASSWORD"
fi

echo "Done."
