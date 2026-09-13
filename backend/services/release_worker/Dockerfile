FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app

# Cài system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Cài Python deps
COPY backend/services/release_worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY backend/services/__init__.py ./services/__init__.py
COPY backend/services/release_worker/ ./services/release_worker/
COPY backend/libs/ ./libs/

# Drop root — read-only app surface.
RUN addgroup --system app && adduser --system --ingroup app app
USER app

