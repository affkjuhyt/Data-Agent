# llm-exporter — stdlib-only Prometheus exporter for LLM generation perf.
FROM python:3.12-slim

WORKDIR /app
COPY backend/services/llm-exporter/exporter.py ./exporter.py

# Non-root (release hardening: containers must not run as root).
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 9200
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9200/health')"

CMD ["python", "exporter.py"]
