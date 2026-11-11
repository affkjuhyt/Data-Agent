# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
# Before any COPY: a code change then reuses this layer instead of rerunning it.
RUN addgroup --system app && adduser --system --ingroup app app
COPY backend/services/worker_document/requirements.txt .
# DEBIAN_MIRROR replaces deb.debian.org where the link to it is slow (the inventory sets it;
# empty keeps upstream). apt verifies the archive's signatures, so a mirror cannot alter packages.
ARG DEBIAN_MIRROR=
RUN if [ -n "$DEBIAN_MIRROR" ]; then sed -i "s#^URIs: http://deb.debian.org/debian\$#URIs: $DEBIAN_MIRROR#" /etc/apt/sources.list.d/debian.sources; fi
# The OCR language packs the image carries: a collection's ingest profile names which of them
# a page is read in (GEN-11); a language not installed here is left out of the read, logged.
ARG OCR_LANGS="eng vie"
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
        $(for l in $OCR_LANGS; do printf 'tesseract-ocr-%s ' "$l" | tr _ -; done) \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
COPY backend/libs/__init__.py ./libs/__init__.py
COPY backend/libs/dataagent_db/ ./libs/dataagent_db/
COPY backend/libs/tabular/ ./libs/tabular/
COPY backend/libs/language/ ./libs/language/
COPY backend/libs/pii/ ./libs/pii/
COPY backend/libs/shared/ ./libs/shared/
COPY backend/libs/vectors/ ./libs/vectors/
COPY backend/services/__init__.py ./services/__init__.py
COPY backend/services/worker_document/ ./services/worker_document/
USER app
