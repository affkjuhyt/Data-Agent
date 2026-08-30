# Build context is the repo root (set in compose) so libs/ is reachable.
FROM python:3.12-slim
WORKDIR /app
COPY backend/services/audit-bridge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# shared code now lives in libs/shared; mount it under the bare `shared`
# package name this service imports (import shared.xxx).
COPY backend/libs/shared/ ./shared/
COPY backend/services/audit-bridge/main.py .
# Drop root — the bridge only reads its code and talks to Kafka/HTTP.
RUN addgroup --system app && adduser --system --ingroup app app
USER app
CMD ["python", "-u", "main.py"]
