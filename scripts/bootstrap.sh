#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "missing required command: ${cmd}" >&2
    exit 1
  fi
}

configure_poetry_venv() {
  local target="$1"
  (
    cd "${target}"
    poetry config virtualenvs.in-project true --local
  )
}

echo "==> Validating local toolchain"
require_cmd poetry
require_cmd docker
require_cmd make

echo "==> Configuring Poetry in-project virtualenvs"
configure_poetry_venv "${ROOT_DIR}/shared"
configure_poetry_venv "${ROOT_DIR}/services/payments-api"
configure_poetry_venv "${ROOT_DIR}/services/ledger-worker"

echo "==> Installing dependencies"
(
  cd "${ROOT_DIR}"
  make install
)

echo "==> Bootstrap completed"
echo "Next steps:"
echo "  1) make up"
echo "  2) make migrate"
echo "  3) make test"
echo "  4) make app-test REQUESTS=1000 CONCURRENCY=50 RUNS=3 WARMUP_RUNS=1"
