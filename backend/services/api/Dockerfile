# syntax=docker/dockerfile:1
# The `api` block of ADR-28. Python 3.12 per ADR-29. Build context is the repo root.
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
COPY backend/services/api/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
# /app mirrors backend/: `libs.*` and `services.api.*` resolve as root packages.
# Only the V3 ORM from libs/: the legacy libs carry dependencies the api never uses.
COPY backend/libs/__init__.py ./libs/__init__.py
COPY backend/libs/dataagent_db/ ./libs/dataagent_db/
COPY backend/services/__init__.py ./services/__init__.py
COPY backend/services/api/ ./services/api/
RUN addgroup --system app && adduser --system --ingroup app app
USER app
EXPOSE 8000
CMD ["uvicorn", "services.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
