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

# Code imports itself as `services.release_worker.xxx` and shared code as
# `libs.shared.xxx` — preserve both package paths.
COPY backend/libs/ ./libs/
COPY backend/services/ ./services/

# Drop root — read-only app surface.
RUN addgroup --system app && adduser --system --ingroup app app
USER app

EXPOSE 8210

CMD ["uvicorn", "services.release_worker.main:app", "--host", "0.0.0.0", "--port", "8210"]
