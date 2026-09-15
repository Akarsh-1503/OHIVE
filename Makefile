# LeadForge — top-level developer entry points.
#
# Deliberately GNU Make 3.81 compatible (the version stock macOS ships): no .ONESHELL,
# no recipe heredocs. Sub-makes in backend/ own the Python-specific targets.

.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE   ?= docker compose
WEB_PORT  ?= 3000
API_PORT  ?= 8000

# Only needed if you switch the api service from its named volume to a ./data bind mount:
# the image runs as uid 10001, but a host directory belongs to you. See docker-compose.yml.
export LEADFORGE_UID := $(shell id -u)
export LEADFORGE_GID := $(shell id -g)

.PHONY: help dev test lint build up down logs ps bench e2e clean

help: ## Show this help
	@echo "LeadForge — business cards in, pipeline-ready leads out."
	@echo ""
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  First time here?  make up   ->  http://localhost:$(WEB_PORT)"

# ---------------------------------------------------------------------------- docker

up: ## Build and start the whole stack, then print the URLs
	$(COMPOSE) up -d --build
	@echo ""
	@echo "  UI    http://localhost:$(WEB_PORT)"
	@echo "  API   http://localhost:$(API_PORT)/docs"
	@echo "  logs  make logs      stop  make down"

down: ## Stop the stack (add ARGS=-v to drop the data volume too)
	$(COMPOSE) down $(ARGS)

build: ## Build both container images without starting them
	$(COMPOSE) build

logs: ## Follow the logs of both services
	$(COMPOSE) logs -f

ps: ## Show container status and health
	$(COMPOSE) ps

# ---------------------------------------------------------------------------- local dev

dev: ## Run both dev servers on the host with hot reload (Ctrl-C stops both)
	@$(MAKE) --no-print-directory -C backend install
	@cd frontend && npm install --no-audit --no-fund --silent
	@echo "backend -> http://localhost:$(API_PORT)   frontend -> http://localhost:$(WEB_PORT)"
	@trap 'kill 0' EXIT INT TERM; \
	  ( cd backend && DATA_DIR=../data ./.venv/bin/uvicorn app.main:app --reload --port $(API_PORT) ) & \
	  ( cd frontend && NEXT_PUBLIC_API_BASE=http://localhost:$(API_PORT)/api/v1 npm run dev ) & \
	  wait

# ---------------------------------------------------------------------------- quality

test: ## Backend test suite (offline) + frontend type check
	$(MAKE) --no-print-directory -C backend test
	cd frontend && npx tsc --noEmit

lint: ## ruff over the backend, eslint over the frontend
	$(MAKE) --no-print-directory -C backend lint
	cd frontend && npm run lint

# Playwright starts its own server in mock mode, but reuses anything already listening on
# :3000 — so stop the docker stack first (`make down`) or it will test the wrong app.
e2e: ## Playwright smoke test against the frontend's in-process mock (needs :3000 free)
	cd frontend && npx playwright install --with-deps chromium && npm run e2e

bench: ## Throughput of the non-model pipeline, against the stub provider
	$(MAKE) --no-print-directory -C backend bench

clean: ## Remove containers, images, local data and build caches
	-$(COMPOSE) down --rmi local --remove-orphans
	$(MAKE) --no-print-directory -C backend clean
	rm -rf data frontend/.next frontend/test-results frontend/playwright-report
