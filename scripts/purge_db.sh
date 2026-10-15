#!/usr/bin/env bash
# Wipe ALL academy data rows (keeps schema). Run from backend/ on any host.
#   scripts/purge_db.sh --dry-run
#   scripts/purge_db.sh --yes [--keep-admins]
set -euo pipefail
cd "$(dirname "$0")/.."
PY=".venv/bin/python"; [ -x "$PY" ] || PY=".venv/Scripts/python.exe"; [ -x "$PY" ] || PY="python"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec "$PY" manage.py purge_data "$@"
