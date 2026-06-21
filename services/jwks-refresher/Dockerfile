# Build context is ../services (set in compose) so shared/ is available.
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY jwks-refresher/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ ./shared/
COPY jwks-refresher/main.py .
CMD ["python", "-u", "main.py"]
