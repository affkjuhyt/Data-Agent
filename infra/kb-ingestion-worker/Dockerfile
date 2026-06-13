FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (cached layer).
# Install CPU-only torch/torchvision before Docling so it doesn't pull the ~5GB CUDA
# build — the worker runs Docling on CPU (USE_VLM=false), so CUDA torch is dead weight.
COPY infra/kb-ingestion-worker/requirements.txt /tmp/worker-requirements.txt
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r /tmp/worker-requirements.txt

# Copy only what the worker imports — nothing from app/
COPY services/__init__.py          /app/services/__init__.py
COPY services/log_set_up/          /app/services/log_set_up/
COPY services/database_connector/  /app/services/database_connector/
COPY basemodel/                    /app/basemodel/
COPY infra/kb-ingestion-worker/    /app/infra/kb-ingestion-worker/

# /app        → resolves services.*, basemodel.*
# worker dir  → resolves local imports (config, silver, gold, worker, etc.)
ENV PYTHONPATH=/app:/app/infra/kb-ingestion-worker

CMD ["python", "infra/kb-ingestion-worker/main.py"]
