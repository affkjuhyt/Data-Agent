FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/services/kb_ingestion_worker/requirements.txt /tmp/worker-requirements.txt
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r /tmp/worker-requirements.txt

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && python -m playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*

COPY backend/services/__init__.py          /app/services/__init__.py
COPY backend/libs/                    /app/libs/
COPY backend/services/kb_ingestion_worker/    /app/services/kb_ingestion_worker/

ENV PYTHONPATH=/app

RUN addgroup --system app && adduser --system --ingroup app --home /home/app app \
    && chown -R app:app /home/app
USER app

