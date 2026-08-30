FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (cached layer).
# Install CPU-only torch/torchvision before Docling so it doesn't pull the ~5GB CUDA
# build — the worker runs Docling on CPU (USE_VLM=false), so CUDA torch is dead weight.
COPY backend/services/kb_ingestion_worker/requirements.txt /tmp/worker-requirements.txt
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r /tmp/worker-requirements.txt

# Headless Chromium + its OS libraries for the web (SPA) render path. --with-deps
# installs the apt libraries Chromium needs; the browser lives in the image.
# Install into a world-readable path (not /root/.cache) so the non-root app user
# added below can still find the browser at runtime.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && python -m playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*

# Copy only what the worker imports — nothing from app/
COPY backend/services/__init__.py          /app/services/__init__.py
COPY backend/libs/                    /app/libs/
COPY backend/services/kb_ingestion_worker/    /app/services/kb_ingestion_worker/

# /app        → resolves services.*, libs.*
# worker dir  → resolves local imports (config, silver, gold, worker, etc.)
ENV PYTHONPATH=/app

# Drop root — worker needs a writable HOME for Chromium's user cache/profile;
# the browser itself lives at /ms-playwright (root-owned, world-readable).
RUN addgroup --system app && adduser --system --ingroup app --home /home/app app \
    && chown -R app:app /home/app
USER app

CMD ["python", "-m", "services.kb_ingestion_worker"]
