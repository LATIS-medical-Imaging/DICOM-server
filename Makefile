DC      ?= docker compose
RUN     := $(DC) --profile tools run --rm tools
RUN_TTY := $(DC) --profile tools run --rm -T tools

.PHONY: help build-tools shell check fix lint format typecheck test cov ci up up-cpu up-gpu down logs gpu-check

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
	@echo "  make up        - Start the full stack (auto-detects GPU, falls back to CPU)"
	@echo "  make up-gpu    - Force the GPU overlay (CUDA torch wheels)"
	@echo "  make up-cpu    - Force CPU only, ignoring any GPU"
	@echo "  make gpu-check - Report whether Docker can use a GPU here"
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

# GPU when the host has one and the NVIDIA container runtime is installed,
# CPU otherwise. The overlay is additive, so the fallback is the plain file.
up:
	@FLAGS="$$(./scripts/detect-gpu.sh || true)"; \
	$(DC) $$FLAGS up -d --build

up-gpu:
	$(DC) -f docker-compose.yml -f docker-compose.gpu.yml up -d --build

up-cpu:
	$(DC) -f docker-compose.yml up -d --build

# Prints what the container actually resolved, not what the host has.
gpu-check:
	@./scripts/detect-gpu.sh >/dev/null || true
	@$(DC) exec -T api python -c "from app.core.config import get_settings; from app.core.torch_runtime import device_report; print(device_report(get_settings().deep_segmentation_device))" \
		|| echo "api container not running - start it with 'make up' first"

down:
	$(DC) down

logs:
	$(DC) logs -f api worker
