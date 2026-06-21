FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY services/ ./services/
COPY server/ ./server/
COPY testing/ ./testing/

RUN pip install --no-cache-dir -e . \
    && pip install --no-cache-dir -r server/requirements.txt

WORKDIR /app/server

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
# LEGACY ONLY - archived backend image
# Not part of the supported Docker entrypoint set.
