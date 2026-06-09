#!/usr/bin/env bash
# Inject a timeout toxic: Toxiproxy holds the connection open then drops it without a
# response, forcing the client into a read timeout (and a retry).
# Usage: scripts/inject_timeout.sh [timeout_ms]   (0 = wait until connection closes)
set -euo pipefail

ADMIN="${TOXIPROXY_ADMIN:-http://localhost:8474}"
PROXY="spring-boot-app"
TIMEOUT="${1:-0}"

echo "Injecting timeout=${TIMEOUT}ms on ${PROXY} (via ${ADMIN})"
curl -fsS -X POST "${ADMIN}/proxies/${PROXY}/toxics" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"timeout\",\"type\":\"timeout\",\"attributes\":{\"timeout\":${TIMEOUT}}}" \
  && echo "  ✓ timeout toxic added"
