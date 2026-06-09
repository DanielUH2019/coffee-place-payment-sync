#!/usr/bin/env bash
# End-to-end resilience demo:
#   1. sync a clean batch (happy path),
#   2. re-run it (idempotent replay — no duplicates),
#   3. inject latency + timeout faults and sync again (retries still deliver),
#   4. reset the network.
set -euo pipefail

cd "$(dirname "$0")/.."

run_sync() {
  docker compose run --rm client examples/payments.csv "$@" || true
}

echo "════════════════════════════════════════════════════════════"
echo " 1) Happy path — first sync"
echo "════════════════════════════════════════════════════════════"
run_sync

echo
echo "════════════════════════════════════════════════════════════"
echo " 2) Idempotency — re-run the same file (expect all 'replayed')"
echo "════════════════════════════════════════════════════════════"
run_sync

echo
echo "════════════════════════════════════════════════════════════"
echo " 3) Resilience — inject latency + timeout, then sync"
echo "════════════════════════════════════════════════════════════"
scripts/inject_latency.sh 1500 500
scripts/inject_timeout.sh 0
echo "Faults active. Syncing through Toxiproxy (retries should still deliver)…"
run_sync

echo
echo "════════════════════════════════════════════════════════════"
echo " 4) Reset the network"
echo "════════════════════════════════════════════════════════════"
scripts/reset_toxics.sh
echo "Demo complete."
