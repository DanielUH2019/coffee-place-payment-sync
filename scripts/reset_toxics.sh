#!/usr/bin/env bash
# Remove all toxics from the Central System proxy, restoring a clean network.
set -euo pipefail

ADMIN="${TOXIPROXY_ADMIN:-http://localhost:8474}"
PROXY="spring-boot-app"

echo "Removing all toxics from ${PROXY} (via ${ADMIN})"
toxics=$(curl -fsS "${ADMIN}/proxies/${PROXY}/toxics" | grep -o '"name":"[^"]*"' | sed 's/"name":"//;s/"//' || true)
if [[ -z "${toxics}" ]]; then
  echo "  (none)"
  exit 0
fi
for name in ${toxics}; do
  curl -fsS -X DELETE "${ADMIN}/proxies/${PROXY}/toxics/${name}" && echo "  ✓ removed ${name}"
done
