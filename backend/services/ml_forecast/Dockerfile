# syntax=docker/dockerfile:1
# ml-forecast: fits forecasts for worker-agent (libs/forecast_fit). Stateless: no database driver,
# no client of any other service. Build context is the repo root.
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
RUN addgroup --system app && adduser --system --ingroup app app
COPY backend/services/ml_forecast/requirements.txt ./service-requirements.txt
COPY backend/libs/forecast_fit/requirements.txt ./fit-requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install -r service-requirements.txt -r fit-requirements.txt
COPY backend/libs/__init__.py ./libs/__init__.py
COPY backend/libs/forecast_fit/ ./libs/forecast_fit/
COPY backend/services/__init__.py ./services/__init__.py
COPY backend/services/ml_forecast/ ./services/ml_forecast/
USER app
