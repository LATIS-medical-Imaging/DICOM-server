ARG PYTHON_VERSION=3.11-slim-bookworm

FROM python:${PYTHON_VERSION} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Create virtualenv in a known location so we can copy it verbatim.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY pyproject.toml README.md ./
# CPU wheels by default; the GPU overlay (docker-compose.gpu.yml) overrides
# this with a CUDA index, e.g. https://download.pytorch.org/whl/cu128.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
# torch AND torchvision must come from the same index: installing only torch
# from the CPU index leaves torchvision to resolve from PyPI, and the resulting
# CUDA-variant build cannot register its C++ ops against a +cpu torch
# ("operator torchvision::nms does not exist" the moment smp/timm import it).
# The project install pins its own index: a bare `pip install .` after the
# torch step inherits whatever index state that step left behind, and the
# PyTorch wheel index serves none of the ordinary dependencies ("no version
# satisfies sqlalchemy>=2.0.36 (from versions: none)").
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install torch torchvision --index-url "${TORCH_INDEX_URL}" \
    && python -m pip install --index-url https://pypi.org/simple .
# --- Dev stage: builder + dev extras (ruff, black, mypy, pytest). ----------
# Used by `docker compose run --rm tools ...` for local lint/type/test runs.
FROM builder AS dev
RUN pip install ".[dev]"
WORKDIR /app
CMD ["bash"]

FROM python:${PYTHON_VERSION} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    APP_HOME=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 app \
    && useradd  --system --uid 1001 --gid app --home-dir ${APP_HOME} app

COPY --from=builder /opt/venv /opt/venv

# Segmentation checkpoint cache. Created here (not just declared as a volume)
# so Docker seeds the named volume with app ownership — the container runs as
# uid 1001 and cannot mkdir inside a root-owned volume root.
RUN mkdir -p /var/cache/medical-std/models \
    && chown -R app:app /var/cache/medical-std

WORKDIR ${APP_HOME}
COPY --chown=app:app . .

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health/live || exit 1

# Default command runs the API. Compose overrides it for the worker, and
# scripts/start-api.sh (migrations first) is the managed-platform entrypoint.
# Shell form so UVICORN_WORKERS expands: a single process serialised all pixel
# work platform-wide — see 0.2 in possible_fixes.md.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${API_PORT:-8000} --workers ${UVICORN_WORKERS:-2}"]
