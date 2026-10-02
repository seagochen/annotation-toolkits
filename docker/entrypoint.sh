#!/bin/bash
set -e

# --- GPU sanity line ---------------------------------------------------------
# Logged, not enforced: without a GPU the platform is still a working CPU
# service (only ReID extraction/mining gets slower), but the reason belongs in
# `docker logs` rather than being discovered halfway through a mining run.
if nvidia-smi -L >/dev/null 2>&1; then
  echo "GPU: $(nvidia-smi -L | head -1)"
else
  echo "GPU: none visible -- was the container started with '--gpus all'?"
fi

# --- project registry --------------------------------------------------------
# The server only reads the registry per request, so a missing file would
# otherwise surface as a 500 in the browser with nothing in the logs.
if [ ! -f "${ANNOTATION_PROJECTS_CONFIG}" ]; then
  echo "warning: ${ANNOTATION_PROJECTS_CONFIG} not found -- mount a directory" \
       "containing projects.yaml at /data (see docker/build_and_run.py --data)"
fi

exec uvicorn annotation_platform.server:app --host 0.0.0.0 --port "${PORT:-3000}" "$@"
