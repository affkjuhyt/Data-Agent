FROM python:3.12-slim

WORKDIR /app

# Cài system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Cài Python deps
COPY backend/services/release-worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY backend/services/release-worker/ .
# shared code now lives in libs/shared; mount it under the bare `shared`
# package name this service imports (import shared.xxx).
COPY backend/libs/shared/ ./shared/

# Drop root — read-only app surface.
RUN addgroup --system app && adduser --system --ingroup app app
USER app

EXPOSE 8100

# Chạy FastAPI service qua framework entrypoint.
CMD ["uvicorn", "app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8100"]
