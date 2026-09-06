# llm_exporter — FastAPI Prometheus exporter for LLM generation perf.
FROM python:3.12-slim

WORKDIR /app
COPY backend/services/llm_exporter/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/services/llm_exporter/exporter.py ./exporter.py

# Non-root (release hardening: containers must not run as root).
RUN useradd --create-home --uid 10001 appuser
USER appuser

