# Annotation Toolkits: web UI + API on one port (3000), with a CUDA runtime so
# ReID extraction/mining can use the GPU.
#
#   python3 docker/build_and_run.py --data /path/to/projects-dir
#
# See docs/detailed_design/90_部署与运维.md for mounts and environment.

# --- 1. frontend build -------------------------------------------------------
FROM node:20-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# tsc + vite directly instead of `npm run build`: that script first runs
# generate:api, which imports the Python backend. src/api/openapi.json and
# schema.d.ts are committed, and `npm test` already fails when they drift.
RUN npx tsc --noEmit && npx vite build

# --- 2. runtime --------------------------------------------------------------
# Same base as the GPU dev box: pinned to the host driver's CUDA 12.6, and the
# cudnn flavour because onnxruntime-gpu >=1.19 links cuDNN 9 and silently
# falls back to CPU without it. Runtime, not devel: nothing compiles CUDA here.
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

# 1001 to match the host account that owns the datasets: annotation results
# are written into bind-mounted directories, and a different uid either fails
# to write or leaves root-owned files behind on the host.
ARG APP_UID=1001
ARG APP_GID=1001

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libgl1/libglib: what opencv-python needs at import time.
# NOTE libglib2.0-0t64, not libglib2.0-0 -- renamed in Ubuntu 24.04.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 \
        python3.12-venv \
        libgl1 \
        libglib2.0-0t64 \
        curl \
        ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu 24.04 marks the system python as externally managed (PEP 668).
RUN python3.12 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# CUDA torch first, from PyTorch's own index; ultralytics only asks for a bare
# torch>=1.8 afterwards, so pip keeps the cu126 build instead of the CPU wheel.
RUN pip install --upgrade pip setuptools wheel \
    && pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126

# The backend's runtime + [extract,ultralytics] extras, spelled out because
# the extras name the CPU `onnxruntime`; installing both distributions makes
# them overwrite each other's `onnxruntime` package. Keep in sync with
# backend/pyproject.toml.
RUN pip install \
        "FastAPI>=0.115,<1" \
        "PyYAML>=6" \
        "uvicorn[standard]>=0.30,<1" \
        "numpy>=1.24" \
        "opencv-python>=4.8" \
        "onnxruntime-gpu>=1.19" \
        "ultralytics>=8.1"

COPY backend/ /app/backend/
RUN pip install --no-deps /app/backend
COPY --from=frontend /src/frontend/dist /app/frontend

RUN groupadd -g ${APP_GID} app \
    && useradd -m -u ${APP_UID} -g ${APP_GID} -s /bin/bash app \
    && mkdir -p /data \
    && chown app:app /data

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV ANNOTATION_PROJECTS_CONFIG=/data/projects.yaml \
    ANNOTATION_FRONTEND_DIST=/app/frontend \
    PORT=3000

USER app
WORKDIR /data
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/projects" >/dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
