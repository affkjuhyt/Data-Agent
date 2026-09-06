FROM python:3.12-alpine

WORKDIR /app

RUN apk add --no-cache gcc musl-dev libpq-dev curl

COPY backend/services/auth_api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/services/auth_api/ .
# shared code now lives in libs/shared; mount it under the bare `shared`
# package name this service imports (import shared.xxx).
COPY backend/libs/shared/ ./shared/

# Drop root (alpine syntax) — read-only app surface.
RUN addgroup -S app && adduser -S -G app app
USER app
