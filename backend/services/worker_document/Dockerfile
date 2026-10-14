# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
COPY backend/services/worker_document/requirements.txt .
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
