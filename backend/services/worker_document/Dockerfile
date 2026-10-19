# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
COPY backend/services/worker_document/requirements.txt .
# DEBIAN_MIRROR replaces deb.debian.org where the link to it is slow (the inventory sets it;
# empty keeps upstream). apt verifies the archive's signatures, so a mirror cannot alter packages.
ARG DEBIAN_MIRROR=
RUN if [ -n "$DEBIAN_MIRROR" ]; then sed -i "s#^URIs: http://deb.debian.org/debian\$#URIs: $DEBIAN_MIRROR#" /etc/apt/sources.list.d/debian.sources; fi
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-vie \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
COPY backend/libs/__init__.py ./libs/__init__.py
COPY backend/libs/basemodel/ ./libs/basemodel/
COPY backend/libs/dataagent_db/ ./libs/dataagent_db/
COPY backend/libs/database_connector/ ./libs/database_connector/
COPY backend/libs/shared/ ./libs/shared/
COPY backend/services/__init__.py ./services/__init__.py
COPY backend/services/worker_document/ ./services/worker_document/
RUN addgroup --system app && adduser --system --ingroup app app
USER app
