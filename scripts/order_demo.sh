#!/usr/bin/env bash
# Order-Ahead workflow demo — walks the six graded failure scenarios (F1–F6).
#
# Prereqs (in separate terminals):
#   make up            # external-app + toxiproxy (the live Payments API on :9091)
#   make temporal-up   # Temporal dev server (UI on http://localhost:8233)
#   make order-worker  # the Order-Ahead worker
#
# Then: make order-demo
set -euo pipefail
cd "$(dirname "$0")/.."

order() { (cd client && uv run coffee-order "$@"); }
banner() { echo; echo "════════════════════════════════════════════════════════════"; echo " $*"; echo "════════════════════════════════════════════════════════════"; }

banner "F1 — Payment times out repeatedly, then succeeds (retry + exactly-once)"
scripts/reset_toxics.sh >/dev/null 2>&1 || true
scripts/inject_timeout.sh 0
order start --order-id demo-f1 --brew-sla 5 --pickup-ttl 120
echo "Payment is timing out through Toxiproxy; Temporal is retrying with backoff…"
sleep 6
echo "Healing the network (next payment retry will land)…"
scripts/reset_toxics.sh
sleep 4
order signal demo-f1 ready
order signal demo-f1 collected
sleep 2
order query demo-f1   # expect DONE, one payment id
echo "Check exactly-one charge:  curl -s -H 'Store-Id: store-london-01' 'http://localhost:9091/api/v1/payments?storeId=store-london-01'"

banner "F2 — Payment permanently declined (release inventory, notify, FAILED)"
order start --order-id demo-f2 --decline
sleep 3
order query demo-f2   # expect FAILED

banner "F3 — Out of stock, no substitution response before deadline → cancel"
order start --order-id demo-f3 --out-of-stock --sub-deadline 5
echo "Substitution offered; not responding before the 5s deadline…"
sleep 7
order query demo-f3   # expect CANCELLED

banner "F4 — Customer cancels before drinks are made (refund + release → CANCELLED)"
order start --order-id demo-f4 --brew-sla 120
sleep 3
order signal demo-f4 cancel
sleep 2
order query demo-f4   # expect CANCELLED

banner "F5 — Customer never collects (pickup-expiry timer → ABANDONED)"
order start --order-id demo-f5 --brew-sla 5 --pickup-ttl 5
sleep 2
order signal demo-f5 ready
echo "Drinks ready; not collecting before the 5s pickup deadline…"
sleep 7
order query demo-f5   # expect ABANDONED

banner "F6 — Worker crash mid-order (resume on restart, no double charge)"
cat <<'EOF'
Manual step (durable replay):
  1. order start --order-id demo-f6 --brew-sla 600 --pickup-ttl 600
  2. Ctrl-C the `make order-worker` process while the order is BREWING.
  3. Restart it: make order-worker
  4. cd client && uv run coffee-order query demo-f6   -> same state, payment_id unchanged.
The Temporal dev server retains the workflow history; the worker replays it on restart.
EOF
echo; echo "Demo complete. Inspect every order at http://localhost:8233"
