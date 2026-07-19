# Build context is the repo root (set in compose) so libs/ is reachable.
FROM python:3.12-slim
WORKDIR /app
COPY services/audit-consumer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# shared code now lives in libs/shared; mount it under the bare `shared`
# package name this service imports (import shared.xxx).
COPY libs/shared/ ./shared/
COPY services/audit-consumer/main.py .
# Drop root — the consumer only reads its code and talks to Kafka/DB.
RUN addgroup --system app && adduser --system --ingroup app app
USER app
CMD ["python", "-u", "main.py"]
