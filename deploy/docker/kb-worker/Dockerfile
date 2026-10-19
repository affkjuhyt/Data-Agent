# syntax=docker/dockerfile:1
# kb-worker — one image for the four KB Kafka workers (promote_silver, promote_gold,
# structured_data, conflict_resolution). Each k8s Deployment picks its worker with
# `command: python -m services.kb_backend.workers.<name>.main`, so principals, consumer
# groups, resources and restart policy stay per worker. requirements-worker.txt is the
# union of what they need (silver is the superset).
FROM python:3.11-slim

# DEBIAN_MIRROR replaces deb.debian.org where the link to it is slow (the inventory sets it;
# empty keeps upstream). apt verifies the archive's signatures, so a mirror cannot alter packages.
ARG DEBIAN_MIRROR=
RUN if [ -n "$DEBIAN_MIRROR" ]; then sed -i "s#^URIs: http://deb.debian.org/debian\$#URIs: $DEBIAN_MIRROR#" /etc/apt/sources.list.d/debian.sources; fi
RUN apt-get update && apt-get install -y --no-install-recommends     libgl1 libglib2.0-0 poppler-utils tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY deploy/docker/kb-worker/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu &&     pip install -r /tmp/requirements.txt

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && python -m playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*

COPY backend/services/__init__.py   /app/services/__init__.py
COPY backend/libs/                  /app/libs/
COPY backend/services/kb_backend/  /app/services/kb_backend/

ENV PYTHONPATH=/app

RUN addgroup --system app && adduser --system --ingroup app --home /home/app app     && chown -R app:app /home/app
USER app
