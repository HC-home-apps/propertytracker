#!/bin/zsh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

mkdir -p logs outputs data/raw

export TZ="Australia/Sydney"

if [[ -f ".env" ]]; then
  set -a
  source ".env"
  set +a
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing virtualenv python at .venv/bin/python"
  echo "Create it with: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
  exit 1
fi

LOCK_DIR="/tmp/propertytracker-weekly.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another weekly run appears to be active. Exiting."
  exit 0
fi
trap 'rmdir "$LOCK_DIR" >/dev/null 2>&1 || true' EXIT

LOG_FILE="logs/local-weekly-$(date +%Y%m%d-%H%M%S).log"
echo "Starting local weekly run at $(date)" | tee -a "$LOG_FILE"

NOTIFY_DRY_FLAG=""
if [[ "${LOCAL_DRY_RUN:-false}" == "true" ]]; then
  NOTIFY_DRY_FLAG="--dry-run"
fi

{
  .venv/bin/python -m tracker ingest
  .venv/bin/python -m tracker ingest-google
  .venv/bin/python -m tracker match-provisional
  .venv/bin/python -m tracker enrich --segment revesby_houses --limit 20
  .venv/bin/python -m tracker enrich --segment wollstonecraft_units --limit 20
  .venv/bin/python -m tracker pending --segment revesby_houses || true
  .venv/bin/python -m tracker pending --segment wollstonecraft_units || true
  .venv/bin/python -m tracker compute
  .venv/bin/python -m tracker notify $NOTIFY_DRY_FLAG
  .venv/bin/python -m tracker review-poll || true
  .venv/bin/python -m tracker review-buttons --segment revesby_houses --limit 10 || true
  .venv/bin/python -m tracker review-buttons --segment wollstonecraft_units --limit 10 || true
} 2>&1 | tee -a "$LOG_FILE"

echo "Local weekly run completed at $(date)" | tee -a "$LOG_FILE"
