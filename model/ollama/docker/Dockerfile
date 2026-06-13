# syntax=docker/dockerfile:1
# Multi-stage build — toolchain + full CUDA toolkit stay out of the final image.
# The runtime is python:3.11-slim for BOTH paths; the GPU path adds only the three
# CUDA runtime .so files llama_cpp's libggml-cuda.so actually links against.
# Model weights are NOT baked in — mount them at /models (see docker-compose).
#
# CPU (default):  docker build -t kb-llama-cpu .
# GPU (CUDA 12.2): docker build --build-arg GPU=1 -t kb-llama-gpu .

ARG GPU=0
ARG BUILDER_BASE=python:3.11-slim
ARG RUNTIME_BASE=python:3.11-slim
ARG CUDA_LIB_BASE=nvidia/cuda:12.2.2-runtime-ubuntu22.04

# ─── Stage 1: builder ────────────────────────────────────────────────────────
# Collects every wheel into /wheels. CPU pulls the prebuilt CPU wheel; GPU pulls
# the prebuilt CUDA 12.2 wheel (which bundles libggml-cuda.so). No compilation.
FROM ${BUILDER_BASE} AS builder
ARG GPU
WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN if [ "$GPU" = "1" ]; then \
        pip wheel --no-cache-dir --no-deps --wheel-dir /wheels \
            "https://github.com/abetlen/llama-cpp-python/releases/download/v0.3.28-cu122/llama_cpp_python-0.3.28-py3-none-linux_x86_64.whl" && \
        pip wheel --no-cache-dir --wheel-dir /wheels \
            diskcache jinja2 numpy typing_extensions; \
    else \
        pip wheel --no-cache-dir --wheel-dir /wheels \
            --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
            --only-binary llama-cpp-python \
            "llama-cpp-python==0.3.28"; \
    fi && \
    pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ─── Stage 1b: CUDA runtime libs ─────────────────────────────────────────────
# Source of the specific CUDA .so files. Only pulled when the GPU runtime needs it.
FROM ${CUDA_LIB_BASE} AS cudalibs

# ─── Stage 2: common runtime ─────────────────────────────────────────────────
FROM ${RUNTIME_BASE} AS runtime-common
ARG GPU
WORKDIR /app
# libgomp1 (GNU OpenMP) is required by libllama.so on both paths; libopenblas0 is CPU-only.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    if [ "$GPU" = "0" ]; then \
        apt-get install -y --no-install-recommends libopenblas0; \
    fi && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# CPU final runtime — nothing else needed.
FROM runtime-common AS runtime-0

# GPU final runtime — copy ONLY the three CUDA runtime libs libggml-cuda.so links
# against (verified via ldd). libcuda.so.1 (the driver) is injected at run time by
# the nvidia container runtime, so it is deliberately not shipped here.
FROM runtime-common AS runtime-1
COPY --from=cudalibs /usr/local/cuda/targets/x86_64-linux/lib/libcudart.so.12    /usr/lib/x86_64-linux-gnu/
COPY --from=cudalibs /usr/local/cuda/targets/x86_64-linux/lib/libcublas.so.12    /usr/lib/x86_64-linux-gnu/
COPY --from=cudalibs /usr/local/cuda/targets/x86_64-linux/lib/libcublasLt.so.12  /usr/lib/x86_64-linux-gnu/
RUN ldconfig

# ─── Stage 3: assemble (GPU selects runtime-1, CPU selects runtime-0) ────────
FROM runtime-${GPU} AS runtime
WORKDIR /app

# Bind-mount the wheels from the builder instead of COPYing them — a COPY layer would
# persist the (multi-GB) wheel files in the final image even after they're installed.
RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    pip install --no-cache-dir /wheels/*.whl && rm -rf /root/.cache

COPY . .

VOLUME ["/models"]

ENV MODEL_PATH=/models/model.gguf \
    N_CTX=4096 \
    N_GPU_LAYERS=0 \
    N_THREADS=8 \
    KV_CACHE_TYPE=f16 \
    USE_MMAP=true \
    USE_MLOCK=false \
    VERBOSE=false \
    POOL_SIZE=1 \
    OMP_NUM_THREADS=8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 11434

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:11434/health')"

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "11434"]
