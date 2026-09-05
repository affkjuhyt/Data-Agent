# llm_exporter — FastAPI Prometheus exporter for LLM generation perf.
FROM python:3.12-slim

WORKDIR /app
COPY backend/services/llm_exporter/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/services/llm_exporter/exporter.py ./exporter.py

# Non-root (release hardening: containers must not run as root).
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8270
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9200/health')"

CMD ["uvicorn", "exporter:app", "--host", "0.0.0.0", "--port", "8270"]
