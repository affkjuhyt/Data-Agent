# syntax=docker/dockerfile:1
FROM python:3.11.9-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY backend/services/indicator_api/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

RUN addgroup --system app && adduser --system --ingroup app app

# The code imports itself as `services.indicator_api.*` and reads libs.*, so
# both keep their package path. Other services are not copied: indicator-api
# must not depend on them.
COPY --chown=app:app backend/libs/ ./libs/
COPY --chown=app:app backend/services/__init__.py ./services/__init__.py
COPY --chown=app:app backend/services/indicator_api/ ./services/indicator_api/

USER app
