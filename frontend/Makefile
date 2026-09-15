# Driftless frontend. `make dev` boots the UI against the in-browser mock, so it runs with
# no backend at all; point NEXT_PUBLIC_MOCK=0 at a live API instead.
IMAGE ?= slam-web
PORT ?= 3000

.DEFAULT_GOAL := help
.PHONY: help install dev test screenshots bench lint build clean

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | expand -t22

install: node_modules ## Install dependencies from the lockfile
node_modules: package-lock.json
	npm ci
	npx playwright install chromium
	@touch node_modules

dev: install ## Run the UI on :3000 against the in-browser mock backend
	NEXT_PUBLIC_MOCK=$${NEXT_PUBLIC_MOCK:-1} npx next dev --port $(PORT)

test: install ## Playwright smoke suite, desktop + phone, against the mock
	npx playwright test smoke

screenshots: install ## Regenerate screenshots/ at 1440x900 and 390x844
	npx playwright test screenshots

bench: install ## Measure viewer frame rate at 50 000 points on the host GPU (opens a window)
	E2E_GPU=1 npx playwright test screenshots --project=desktop -g "frame rate"

lint: install ## eslint + tsc --noEmit
	npm run lint
	npm run typecheck

build: ## Build the container image (context is this directory)
	docker build -t $(IMAGE) .

clean: ## Remove build output and capture artefacts
	rm -rf .next node_modules test-results playwright-report tsconfig.tsbuildinfo
