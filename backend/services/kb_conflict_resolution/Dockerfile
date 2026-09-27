# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

COPY backend/services/kb_conflict_resolution/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install -r /tmp/requirements.txt

COPY backend/services/__init__.py   /app/services/__init__.py
COPY backend/libs/                  /app/libs/
COPY backend/services/kb_conflict_resolution/         /app/services/kb_conflict_resolution/

ENV PYTHONPATH=/app

RUN addgroup --system app && adduser --system --ingroup app --home /home/app app     && chown -R app:app /home/app
USER app
