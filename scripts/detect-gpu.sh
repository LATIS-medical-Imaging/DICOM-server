#!/usr/bin/env sh
# Decide whether this host can give a container a GPU.
#
# Prints the compose flags to use and exits 0 when it can, prints nothing and
# exits 1 when it can't. Both conditions must hold: an NVIDIA GPU the driver
# can see, and a Docker runtime able to pass it through (nvidia-container-
# toolkit). A GPU with no toolkit is a CPU host as far as containers care.
set -eu

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
GPU_FILE="${GPU_FILE:-docker-compose.gpu.yml}"

log() { [ "${QUIET:-0}" = "1" ] || printf '%s\n' "$*" >&2; }

if [ "${FORCE_CPU:-0}" = "1" ]; then
    log "detect-gpu: FORCE_CPU=1 — using CPU."
    exit 1
fi

if ! command -v nvidia-smi >/dev/null 2>&1; then
    log "detect-gpu: no nvidia-smi — using CPU."
    exit 1
fi

if ! nvidia-smi -L >/dev/null 2>&1; then
    log "detect-gpu: nvidia-smi present but no usable GPU — using CPU."
    exit 1
fi

# The toolkit registers either a `nvidia` runtime or CDI devices; accept both.
if ! docker info 2>/dev/null | grep -qiE 'runtimes:.*nvidia|nvidia\.com/gpu'; then
    log "detect-gpu: GPU found but Docker has no NVIDIA runtime"
    log "            (install nvidia-container-toolkit) — using CPU."
    exit 1
fi

log "detect-gpu: GPU available — $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
printf -- '-f %s -f %s\n' "$COMPOSE_FILE" "$GPU_FILE"
