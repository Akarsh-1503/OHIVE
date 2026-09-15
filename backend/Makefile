# Deliberately GNU Make 3.81 compatible (stock macOS): no .ONESHELL, no recipe heredocs.
.DEFAULT_GOAL := help
SHELL := /bin/bash

VENV    ?= .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
IMAGE   ?= ak-project-leadforge-api
PORT    ?= 8000
CARDS   ?= 25

.PHONY: help venv install dev test lint fmt bench build run clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

$(VENV)/bin/python:
	python3.11 -m venv $(VENV)
	$(PIP) install --upgrade pip setuptools wheel

venv: $(VENV)/bin/python ## Create the virtualenv

install: venv ## Install the project with dev extras
	@$(PIP) install -q -e '.[dev]'

dev: install ## Run the API with auto-reload on :8000 (stub VLM by default)
	DATA_DIR=$${DATA_DIR:-./data} $(VENV)/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port $(PORT)

test: install ## Run the full test suite (offline, no GPU)
	$(PY) -m pytest -q

lint: install ## Lint with ruff
	$(VENV)/bin/ruff check .

fmt: install ## Apply ruff's safe autofixes
	$(VENV)/bin/ruff check --fix .

# Measures the whole request path — upload, preprocess, normalise, dedup, persist — against
# the stub provider, so the number isolates our overhead from model inference time.
define BENCH_SCRIPT
import io, os, tempfile, time
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from app.main import create_app
from app.models import Settings

def card():
    image = Image.new("RGB", (1400, 850), (252, 252, 250))
    draw = ImageDraw.Draw(image)
    lines = ["PRIYA RAGHAVAN", "VP of Partnerships", "Northwind Robotics",
             "Bengaluru, KA, India", "+91 80 4718 2200", "priya@northwind.io"]
    for index, line in enumerate(lines):
        for dx in range(3):
            for dy in range(3):
                draw.text((60 + dx, 60 + index * 70 + dy), line, fill=(20, 20, 28))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=92)
    return buffer.getvalue()

count = int(os.environ.get("CARDS", "25"))
payload = card()
settings = Settings(vlm_provider="stub", data_dir=tempfile.mkdtemp(), vlm_concurrency=6,
                    log_level="WARNING")
with TestClient(create_app(settings)) as client:
    started = time.monotonic()
    batch_id = client.post("/api/v1/batches", files=[
        ("files", ("card_%02d.jpg" % i, payload, "image/jpeg")) for i in range(count)
    ]).json()["batch_id"]
    while True:
        batch = client.get("/api/v1/batches/" + batch_id).json()
        if batch["status"] in ("completed", "partial", "failed"):
            break
        time.sleep(0.02)
    elapsed = time.monotonic() - started
    export = time.monotonic()
    client.get("/api/v1/batches/" + batch_id + "/export.xlsx")
    export = time.monotonic() - export
per_card = sum(lead["processing_ms"] for lead in batch["leads"]) / count
print("%d cards in %.2fs  ->  %.1f cards/s" % (count, elapsed, count / elapsed))
print("mean per-card pipeline time: %.0f ms (stub VLM, so this is our overhead only)" % per_card)
print("xlsx export: %.0f ms" % (export * 1000))
endef
export BENCH_SCRIPT

bench: install ## Measure end-to-end throughput against the stub provider
	@CARDS=$(CARDS) $(PY) -c "$$BENCH_SCRIPT"

build: ## Build the container image
	docker build -t $(IMAGE):latest .

run: build ## Run the container on :$(PORT) with a local data volume
	docker run --rm -p $(PORT):8000 -v leadforge-data:/data --name leadforge-api $(IMAGE):latest

clean: ## Remove caches, the venv and local data
	rm -rf $(VENV) data .pytest_cache .ruff_cache app/__pycache__ tests/__pycache__ *.egg-info
