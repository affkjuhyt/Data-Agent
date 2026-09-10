FROM python:3.12-alpine

WORKDIR /app
ENV PYTHONPATH=/app

RUN apk add --no-cache gcc musl-dev libpq-dev curl

COPY backend/services/auth_api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code imports itself as `services.auth_api.xxx` and shared code as
# `libs.shared.xxx` — preserve both package paths.
COPY backend/libs/ ./libs/
COPY backend/services/ ./services/

# Drop root (alpine syntax) — read-only app surface.
RUN addgroup -S app && adduser -S -G app app
USER app
