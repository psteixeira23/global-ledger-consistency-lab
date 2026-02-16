#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${ROOT_DIR}/services/payments-api"
poetry run python -m payments_api.db.migrate
echo "Schema migration completed (MIGRATE_RECREATE_SCHEMA=${MIGRATE_RECREATE_SCHEMA:-1})."
