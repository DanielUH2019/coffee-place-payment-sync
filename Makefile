SHELL := /bin/bash
CSV ?= examples/payments.csv

LB_COMPOSE := -f docker-compose.yml -f docker-compose.lb.yml
LB_REPLICAS ?= 3

.PHONY: help up down logs build sync demo \
        inject-latency inject-timeout reset-toxics \
        test test-integration lint clean \
        lb-test lb-up lb-down lb-demo test-lb-integration

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Build + start the Central System (external-app + toxiproxy)
	docker compose up --build -d external-app toxiproxy

down: ## Stop and remove the stack
	docker compose down -v

logs: ## Tail logs for the running stack
	docker compose logs -f

build: ## Build all images (external-app + client)
	docker compose build external-app
	docker compose --profile tools build client

sync: ## Sync a CSV through the dockerised client (override: make sync CSV=path)
	docker compose run --rm client $(CSV)

demo: ## Run the full happy-path / idempotency / fault-injection demo
	./scripts/demo.sh

inject-latency: ## Inject a latency toxic (make inject-latency MS=2000)
	./scripts/inject_latency.sh $(MS)

inject-timeout: ## Inject a timeout toxic
	./scripts/inject_timeout.sh

reset-toxics: ## Remove all injected toxics
	./scripts/reset_toxics.sh

test: ## Run unit tests (no Docker needed)
	cd client && uv run pytest -m "not integration"

test-integration: up ## Run live end-to-end tests against the running stack
	cd client && COFFEE_SYNC_BASE_URL=http://localhost:9091 \
		TOXIPROXY_ADMIN=http://localhost:8474 \
		uv run pytest -m integration -v

clean: ## Remove generated output
	rm -rf out client/.venv client/.pytest_cache

# ── Redirect load balancer (homework 2) ──────────────────────────────────────

lb-test: ## Run the load balancer's Go unit tests
	cd redirect-lb && go test ./...

lb-up: ## Build + start the LB in front of $(LB_REPLICAS) app replicas
	# external-app (port 8080) is started too so the integration suite's
	# require_stack guard passes; the LB itself only discovers external-app-lb.
	docker compose $(LB_COMPOSE) up -d --build \
		--scale external-app-lb=$(LB_REPLICAS) external-app external-app-lb lb

lb-down: ## Stop and remove the LB stack
	docker compose $(LB_COMPOSE) down -v

lb-demo: ## Discovery + distribution + end-to-end + self-healing demo
	REPLICAS=$(LB_REPLICAS) ./scripts/lb_demo.sh

test-lb-integration: lb-up ## Live load-balancer integration tests (drives docker compose)
	cd client && LB_URL=http://localhost:8090 \
		LB_COMPOSE="$(LB_COMPOSE)" \
		uv run pytest -m integration tests/integration/test_load_balancer.py -v
