SHELL := /bin/bash
CSV ?= examples/payments.csv

.PHONY: help up down logs build sync demo \
        inject-latency inject-timeout reset-toxics \
        test test-integration lint clean

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
