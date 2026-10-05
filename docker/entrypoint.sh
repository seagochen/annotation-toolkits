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

# --- workspace ----------------------------------------------------------------
# No registry check: projects are created from the web UI, and a workspace
# without projects.yaml is simply an empty project list.
echo "workspace: ${ANNOTATION_WORKSPACE}" \
     "(linkable directories: ${ANNOTATION_IMPORT_ROOTS:-workspace only})"

exec uvicorn annotation_platform.server:app --host 0.0.0.0 --port "${PORT:-3000}" "$@"
