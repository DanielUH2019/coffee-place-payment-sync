#!/usr/bin/env bash
# Redirect load-balancer demo:
#   1. bring up N payment-app replicas + the LB,
#   2. show the discovered backend table,
#   3. fire 300 requests at the LB and histogram the redirect targets (distribution),
#   4. run the real sync client THROUGH the LB (follows the 307 to a backend) and
#      verify the payments summed across all replicas == rows the client created,
#   5. stop one replica and show self-healing (it drops out of the table).
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.lb.yml)
REPLICAS="${REPLICAS:-3}"
LB="http://localhost:8090"   # host-reachable ingress (for curl from the host)
LB_INTERNAL="http://lb:8090" # in-network name (for the dockerised client)
STORE="lb-demo-$$"           # only used for the read-only distribution probes
CSV_STORE="coffee-place-001" # the store_id baked into examples/payments.csv

line() { printf '════════════════════════════════════════════════════════════\n'; }

line; echo " 1) Start $REPLICAS app replicas + the redirect LB"; line
# --force-recreate so each replica's in-memory store starts empty -> the
# cross-replica payment sum below is exactly the number the client creates.
"${COMPOSE[@]}" up -d --build --force-recreate --scale external-app-lb="$REPLICAS" external-app-lb lb
# Give discovery + health checks a moment to populate the table.
sleep 8

line; echo " 2) Backend table the LB discovered (via Docker DNS)"; line
curl -s "$LB/__lb/backends" | sed 's/},{/}\n{/g'

line; echo " 3) Distribution — 300 requests, histogram of redirect targets"; line
for _ in $(seq 1 300); do
  curl -s -o /dev/null -w "%{redirect_url}\n" "$LB/api/v1/payments?storeId=$STORE" -H "Store-Id: $STORE"
done | sed -E 's#https?://([^/]+)/.*#\1#' | sort | uniq -c | sort -rn

line; echo " 4) End-to-end: run the sync client THROUGH the LB"; line
# --build so the client image always carries the latest code (it follows the 307).
"${COMPOSE[@]}" run --rm --build --no-deps \
  -e COFFEE_SYNC_BASE_URL="$LB_INTERNAL" \
  client examples/payments.csv || true

echo
echo "Verifying payments summed across all replicas (each has its own in-memory store):"
total=0
for cid in $("${COMPOSE[@]}" ps -q external-app-lb); do
  # `|| true`: grep exits 1 when a replica holds none of this store's payments,
  # which would otherwise trip `set -o pipefail` and abort the demo.
  n=$(docker exec "$cid" curl -s -H "Store-Id: $CSV_STORE" \
        "http://localhost:8080/api/v1/payments?storeId=$CSV_STORE" | { grep -o paymentId || true; } | wc -l | tr -d ' ')
  short="$(docker inspect -f '{{.Name}}' "$cid" | sed 's#^/##')"
  echo "  $short: $n payment(s)"
  total=$((total + n))
done
echo "  TOTAL across replicas: $total"

line; echo " 5) Self-healing — stop one replica, watch the table shrink"; line
victim="$("${COMPOSE[@]}" ps -q external-app-lb | head -n1)"
echo "Stopping replica $victim …"
docker stop "$victim" >/dev/null
sleep 6
echo "Backend table after eviction:"
curl -s "$LB/__lb/backends" | sed 's/},{/}\n{/g'

echo
echo "Demo complete. Tear down with:  docker compose -f docker-compose.yml -f docker-compose.lb.yml down -v"
