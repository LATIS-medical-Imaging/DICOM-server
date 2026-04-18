DC      ?= docker compose
RUN     := $(DC) --profile tools run --rm tools
RUN_TTY := $(DC) --profile tools run --rm -T tools

.PHONY: help build-tools shell check fix lint format typecheck test cov ci up down logs

help:
	@echo "All targets run inside Docker - no local Python required."
	@echo ""
	@echo "Static analysis:"
	@echo "  make ci        - Full local mirror of GitHub CI (lint+format+types+tests)"
	@echo "  make check     - Lint + format + types"
	@echo "  make fix       - Auto-fix lint + formatting"
	@echo "  make lint      - Ruff lint only"
	@echo "  make format    - Black format check only"
	@echo "  make typecheck - Mypy only"
	@echo "  make test      - Pytest"
	@echo "  make cov       - Pytest with coverage"
	@echo ""
	@echo "Stack management:"
	@echo "  make up        - Start the full stack (api, worker, db, redis, minio)"
	@echo "  make down      - Stop the stack"
	@echo "  make logs      - Tail api+worker logs"
	@echo "  make shell     - Open a bash shell in the tools container"
	@echo "  make build-tools - Rebuild the tools image (after pyproject changes)"

build-tools:
	$(DC) --profile tools build tools

shell:
	$(RUN) bash

check: lint format typecheck
	@echo "All static checks passed."

fix:
	$(RUN_TTY) ruff check --fix .
	$(RUN_TTY) black .

lint:
	$(RUN_TTY) ruff check .

format:
	$(RUN_TTY) black --check .

typecheck:
	$(RUN_TTY) mypy app

test:
	$(RUN_TTY) pytest

cov:
	$(RUN_TTY) pytest --cov=app --cov-report=term-missing

ci: check test
	@echo "Local CI mirror passed - safe to push."

up:
	$(DC) up -d --build

down:
	$(DC) down

logs:
	$(DC) logs -f api worker
