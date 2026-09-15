# Driftless backend. `make dev` boots the API with the stub engine so it runs standalone.
PY ?= python3.11
VENV := .venv
BIN := $(VENV)/bin
IMAGE ?= slam-api

.DEFAULT_GOAL := help
.PHONY: help venv dev test lint build clean

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | expand -t22

venv: $(BIN)/python ## Create the virtualenv and install dev dependencies
$(BIN)/python:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --quiet --upgrade pip
	$(BIN)/pip install --quiet -e '.[dev]'

dev: venv ## Run the API on :8000 with the stub SLAM backend
	DATA_DIR=$${DATA_DIR:-./data} SLAM_BACKEND=$${SLAM_BACKEND:-stub} \
	$(BIN)/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test: venv ## Run the full pytest suite offline against the stub backend
	SLAM_BACKEND=stub $(BIN)/pytest -q

lint: venv ## ruff check
	$(BIN)/ruff check .

build: ## Build the container image (context is assignment2/, one level up)
	docker build -f Dockerfile -t $(IMAGE) ..

clean: ## Remove caches, build output and the local data dir
	rm -rf $(VENV) .pytest_cache .ruff_cache data *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
