.PHONY: help install install-server install-probe install-frontend \
        sync lock migrate revision upgrade downgrade \
        server probe probe-run probe-build probe-buildx probe-push frontend \
        test test-server test-probe \
        archive admin-create dist clean docker-build docker-up docker-down docker-logs

SHELL := /usr/bin/env bash

ROOT_DIR       := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
SERVER_DIR     := $(ROOT_DIR)/server
PROBE_DIR      := $(ROOT_DIR)/probe
FRONTEND_DIR   := $(ROOT_DIR)/frontend

PYTHON         ?= python3
UV             ?= uv
DOCKER         ?= docker
PNPM           ?= pnpm

PROBE_IMAGE          ?= ghcr.io/gorcer/xspeed-probe
PROBE_IMAGE_TAG      ?= latest
PROBE_PLATFORMS      ?= linux/amd64,linux/arm64

DATABASE_URL   ?= $(shell grep -E '^DATABASE_URL=' $(ROOT_DIR)/.env 2>/dev/null | cut -d= -f2-)
ifeq ($(strip $(DATABASE_URL)),)
DATABASE_URL := postgresql+asyncpg://xspeed:xspeed@localhost:5432/xspeed
endif

help: ## Show this help.
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_.-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: install-server install-probe install-frontend ## Install all workspace dependencies.

install-server: ## Install server (Python) deps via uv.
	cd $(SERVER_DIR) && $(UV) sync

install-probe: ## Install probe (Python) deps via uv.
	cd $(PROBE_DIR) && $(UV) sync

install-frontend: ## Install frontend deps via pnpm.
	cd $(FRONTEND_DIR) && $(PNPM) install --frozen-lockfile || $(PNPM) install

sync: ## Sync the full Python workspace (members + dev groups).
	$(UV) sync --all-packages

lock: ## Refresh uv.lock from pyproject.toml files.
	$(UV) lock

migrate: ## Apply database migrations.
	cd $(SERVER_DIR) && $(UV) run alembic upgrade head

revision: ## Create a new Alembic revision (msg=...).
	cd $(SERVER_DIR) && $(UV) run alembic revision --autogenerate -m "$(msg)"

upgrade: migrate ## Alias for migrate.

downgrade: ## Roll back the last migration.
	cd $(SERVER_DIR) && $(UV) run alembic downgrade -1

server: ## Run the FastAPI backend (dev).
	cd $(SERVER_DIR) && $(UV) run uvicorn xspeed_server.app:create_app --host 0.0.0.0 --port 8000 --reload --factory

probe: probe-buildx ## Build and push the multi-arch probe image.

probe-run: ## Run the local probe without Docker.
	cd $(PROBE_DIR) && $(UV) run xspeed-probe

probe-build: ## Build a single-arch probe image (host architecture).
	$(DOCKER) build -t $(PROBE_IMAGE):$(PROBE_IMAGE_TAG) -f $(PROBE_DIR)/Dockerfile $(PROBE_DIR)

probe-buildx: ## Build and push the multi-arch probe image (linux/amd64,linux/arm64).
	$(DOCKER) buildx build \
	    --platform $(PROBE_PLATFORMS) \
	    -t $(PROBE_IMAGE):$(PROBE_IMAGE_TAG) \
	    -f $(PROBE_DIR)/Dockerfile \
	    --push \
	    $(PROBE_DIR)

frontend: ## Run the SvelteKit dev server.
	cd $(FRONTEND_DIR) && $(PNPM) dev

test: test-server test-probe ## Run all tests.

test-server: ## Run server tests.
	cd $(SERVER_DIR) && $(UV) run pytest

test-probe: ## Run probe tests.
	cd $(PROBE_DIR) && $(UV) run pytest

archive: ## Run a single pass of the task_results archive job (DAT-325.7).
	cd $(SERVER_DIR) && $(UV) run python -m xspeed_server.app.tasks.cli

admin-create: ## Create or update an admin user (DAT-325.8). Args: email=, password=.
	cd $(SERVER_DIR) && $(UV) run python -m xspeed_server.app.admin_cli create --email $(email) --password $(password)

dist: ## Build release tarballs into dist/.
	mkdir -p dist
	tar --exclude='./dist' --exclude='./.venv' --exclude='./**/.venv' \
	    --exclude='./**/__pycache__' --exclude='./**/node_modules' \
	    --exclude='./.git' --exclude='./**/.mypy_cache' --exclude='./**/.ruff_cache' \
	    --exclude='./**/.pytest_cache' --exclude='./.svelte-kit' --exclude='./**/build' \
	    -czf dist/xspeed-source.tar.gz -C $(ROOT_DIR) .
	tar -czf dist/xspeed-repo.tar.gz -C $(ROOT_DIR) .git

clean: ## Remove build artefacts.
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . -type d -name '.mypy_cache' -prune -exec rm -rf {} +
	find . -type d -name '.ruff_cache' -prune -exec rm -rf {} +
	find . -type d -name '.pytest_cache' -prune -exec rm -rf {} +
	rm -rf dist

docker-build: ## Build all docker images.
	$(DOCKER) compose build

docker-up: ## Start docker compose stack.
	$(DOCKER) compose up -d

docker-down: ## Stop docker compose stack.
	$(DOCKER) compose down

docker-logs: ## Tail docker compose logs.
	$(DOCKER) compose logs -f