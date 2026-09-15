# Driftless — top-level developer entry points.
#
# Deliberately GNU Make 3.81 compatible (the version stock macOS ships): no .ONESHELL,
# no recipe heredocs. Sub-makes in backend/ and frontend/ own the language-specific
# targets; this file owns the whole stack and the SLAM engine's own suites.

.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE   ?= docker compose
WEB_PORT  ?= 3000
API_PORT  ?= 8000
PY        ?= python3.11
VENV      := .venv
BIN       := $(VENV)/bin
# The SLAM engine, its tests and the benchmark have no pyproject of their own at this level,
# so they borrow the engine's ruff configuration explicitly.
RUFF_CFG  := slam/pyproject.toml

# Only needed if you switch the api service from its named volume to a ./data bind mount:
# the image runs as uid 10001, but a host directory belongs to you. See docker-compose.yml.
export DRIFTLESS_UID := $(shell id -u)
export DRIFTLESS_GID := $(shell id -g)

.PHONY: help up up-real down dev test test-slam lint build bench bench-quick logs ps clean

help: ## Show this help
	@echo "Driftless — monocular video in, metric-consistent sparse map out."
	@echo ""
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-11s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  First time here?  make up   ->  http://localhost:$(WEB_PORT)"

# ---------------------------------------------------------------------------- docker

up: ## Build and start the stack with the stub engine, then print the URLs
	$(COMPOSE) up -d --build
	@echo ""
	@echo "  UI     http://localhost:$(WEB_PORT)"
	@echo "  API    http://localhost:$(API_PORT)/docs"
	@echo "  engine stub (synthetic).  Real SLAM:  make up-real"
	@echo "  logs   make logs      stop  make down"

up-real: ## Same, but with the real SLAM engine (what the benchmark measures)
	SLAM_BACKEND=real $(COMPOSE) up -d --build
	@echo ""
	@echo "  UI     http://localhost:$(WEB_PORT)"
	@echo "  API    http://localhost:$(API_PORT)/docs"
	@echo "  engine real"

down: ## Stop the stack (ARGS=-v also drops the driftless-data volume)
	$(COMPOSE) down $(ARGS)

build: ## Build both container images without starting them
	$(COMPOSE) build

logs: ## Follow the logs of both services
	$(COMPOSE) logs -f

ps: ## Show container status and health
	$(COMPOSE) ps

# ---------------------------------------------------------------------------- local dev

$(BIN)/python:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --quiet --upgrade pip
	$(BIN)/pip install --quiet -e './slam[dev]' ruff

dev: ## Run both dev servers on the host with hot reload (Ctrl-C stops both)
	@$(MAKE) --no-print-directory -C backend venv
	@cd frontend && npm install --no-audit --no-fund --silent
	@echo "backend -> http://localhost:$(API_PORT)   frontend -> http://localhost:$(WEB_PORT)"
	@trap 'kill 0' EXIT INT TERM; \
	  ( cd backend && DATA_DIR=../data SAMPLES_DIR=../samples SLAM_BACKEND=$${SLAM_BACKEND:-real} \
	      ./.venv/bin/uvicorn app.main:app --reload --port $(API_PORT) ) & \
	  ( cd frontend && NEXT_PUBLIC_MOCK=0 \
	      NEXT_PUBLIC_API_BASE=http://localhost:$(API_PORT)/api/v1 npx next dev --port $(WEB_PORT) ) & \
	  wait

# ---------------------------------------------------------------------------- quality

test: test-slam ## SLAM engine tests + backend tests (offline) + frontend type check
	$(MAKE) --no-print-directory -C backend test
	cd frontend && npx tsc --noEmit

test-slam: $(BIN)/python ## SLAM engine tests only (geometry, optimisation, features, pipeline)
	$(BIN)/pytest tests -q

lint: $(BIN)/python ## ruff over the engine and the backend, eslint + tsc over the frontend
	$(BIN)/ruff check --config $(RUFF_CFG) slam tests bench
	$(MAKE) --no-print-directory -C backend lint
	$(MAKE) --no-print-directory -C frontend lint

# ---------------------------------------------------------------------------- benchmark

bench: $(BIN)/python ## Full benchmark: 3 clips x 6 variants x 5 repeats -> bench/results.json
	$(BIN)/python bench/benchmark.py --repeats 5 --ablate

bench-quick: $(BIN)/python ## One repeat of the shipped defaults on all three clips
	$(BIN)/python bench/benchmark.py --repeats 1 --out /tmp/driftless-bench.json

# ---------------------------------------------------------------------------- housekeeping

clean: ## Remove containers, images, local data and build caches
	-$(COMPOSE) down --rmi local --remove-orphans
	$(MAKE) --no-print-directory -C backend clean
	$(MAKE) --no-print-directory -C frontend clean
	rm -rf data $(VENV) .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
