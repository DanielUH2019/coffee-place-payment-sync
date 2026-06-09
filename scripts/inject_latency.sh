#!/usr/bin/env bash
# Inject a latency toxic into the Toxiproxy proxy that fronts the Central System.
# Usage: scripts/inject_latency.sh [latency_ms] [jitter_ms]
set -euo pipefail

ADMIN="${TOXIPROXY_ADMIN:-http://localhost:8474}"
PROXY="spring-boot-app"
LATENCY="${1:-2000}"
JITTER="${2:-500}"

echo "Injecting latency=${LATENCY}ms jitter=${JITTER}ms on ${PROXY} (via ${ADMIN})"
curl -fsS -X POST "${ADMIN}/proxies/${PROXY}/toxics" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"latency\",\"type\":\"latency\",\"attributes\":{\"latency\":${LATENCY},\"jitter\":${JITTER}}}" \
  && echo "  ✓ latency toxic added"
