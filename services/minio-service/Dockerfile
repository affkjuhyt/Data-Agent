FROM python:3.12-alpine

WORKDIR /app

RUN apk add --no-cache gcc musl-dev curl

COPY services/minio-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# libs/ keeps its real path rather than being flattened to `shared/`:
# libs/shared/auth.py imports libs.shared.service_identity absolutely, so the
# flattened name cannot carry it, and this service now needs the shared
# authentication decision.
COPY libs/__init__.py ./libs/__init__.py
COPY libs/shared/ ./libs/shared/
COPY services/minio-service/ .

# Drop root (alpine syntax) — read-only app surface.
RUN addgroup -S app && adduser -S -G app app
USER app

EXPOSE 8400

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8400"]
