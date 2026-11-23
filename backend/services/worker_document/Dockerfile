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
# DI-03: Docling (requirements.txt) runs its layout and table models on torch. The CPU wheels come
# from PyTorch's own index first, so pip never pulls the CUDA build (gigabytes the pod cannot use).
RUN --mount=type=cache,target=/root/.cache/pip pip install \
        --index-url https://download.pytorch.org/whl/cpu torch==2.15.0+cpu torchvision==0.29.2+cpu
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
# The models Docling reads, fetched at build time: the cluster has no egress, so nothing is
# downloaded at run time (HF_HUB_OFFLINE). Layout and TableFormer for every PDF, RapidOCR for the
# bake-off's B2 system (DOCUMENT_INTELLIGENCE_SPEC §3.2, §11); Tesseract is the apt package above.
# In the image rather than on a PVC: the layer is cached across builds and a pod needs no copy job.
RUN docling-tools models download -o /models/docling layout tableformer rapidocr \
    && chmod -R a+rX /models/docling
# Where the models are: a fact of this image's layout. That the hub is never reached at run time
# (HF_HUB_OFFLINE, TRANSFORMERS_OFFLINE, HF_HOME on /tmp) is the deployment's, in its ConfigMap.
ENV DOCLING_ARTIFACTS_PATH=/models/docling
COPY backend/libs/__init__.py ./libs/__init__.py
COPY backend/libs/dataagent_db/ ./libs/dataagent_db/
COPY backend/libs/metrics/ ./libs/metrics/
COPY backend/libs/tabular/ ./libs/tabular/
COPY backend/libs/language/ ./libs/language/
# DI-10: what a document file is, by its content (magic, zip directory), then its name.
COPY backend/libs/document_intake/ ./libs/document_intake/
COPY backend/libs/pii/ ./libs/pii/
COPY backend/libs/shared/ ./libs/shared/
COPY backend/libs/vectors/ ./libs/vectors/
COPY backend/services/__init__.py ./services/__init__.py
COPY backend/services/worker_document/ ./services/worker_document/
USER app
