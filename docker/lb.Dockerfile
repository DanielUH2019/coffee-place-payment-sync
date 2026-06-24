# Multi-stage build for the redirect load balancer.
#
# Build context is ./redirect-lb (see docker-compose.lb.yml). The module has no
# third-party dependencies, so the result is a tiny static binary.

# ---- build stage ------------------------------------------------------------
FROM golang:1.25-alpine AS build
WORKDIR /src
COPY go.mod ./
RUN go mod download
COPY . .
# CGO off -> pure-Go DNS resolver, which is exactly what we want for the
# Docker-DNS service discovery (net.LookupHost reads the injected resolv.conf).
RUN CGO_ENABLED=0 go build -o /lb .

# ---- runtime stage ----------------------------------------------------------
FROM alpine:3.20 AS runtime
RUN apk add --no-cache wget
COPY --from=build /lb /usr/local/bin/lb
EXPOSE 8090
# The status endpoint is always answerable (even with zero backends) -> liveness.
HEALTHCHECK --interval=5s --timeout=3s --retries=5 \
    CMD wget -qO- http://localhost:8090/__lb/backends >/dev/null 2>&1 || exit 1
ENTRYPOINT ["lb"]
