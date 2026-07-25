FROM python:3.12-alpine

WORKDIR /app

RUN apk add --no-cache gcc musl-dev curl

COPY services/minio-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# shared code now lives in libs/shared; mount it under the bare `shared`
# package name this service imports (import shared.xxx).
COPY libs/shared/ ./shared/
COPY services/minio-service/ .

# Drop root (alpine syntax) — read-only app surface.
RUN addgroup -S app && adduser -S -G app app
USER app

EXPOSE 8400

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8400"]
