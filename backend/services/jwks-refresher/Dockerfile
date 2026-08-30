# Build context is the repo root (set in compose) so libs/ is reachable.
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY backend/services/jwks-refresher/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# shared code now lives in libs/shared; mount it under the bare `shared`
# package name this service imports (import shared.xxx).
COPY backend/libs/shared/ ./shared/
COPY backend/services/jwks-refresher/main.py .
# Drop root — read-only app surface.
RUN addgroup --system app && adduser --system --ingroup app app
USER app
CMD ["python", "-u", "main.py"]
