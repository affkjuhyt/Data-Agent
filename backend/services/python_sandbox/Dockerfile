# syntax=docker/dockerfile:1
# python-sandbox: runs the dynamic agent's analysis code (libs/code_sandbox). Only Python, pandas
# and numpy; no database driver, no client of any other service. Build context is the repo root.
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
RUN addgroup --system app && adduser --system --ingroup app app
COPY backend/services/python_sandbox/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
COPY backend/libs/__init__.py ./libs/__init__.py
COPY backend/libs/code_sandbox/ ./libs/code_sandbox/
COPY backend/services/__init__.py ./services/__init__.py
COPY backend/services/python_sandbox/ ./services/python_sandbox/
USER app
EXPOSE 8140
CMD ["python", "-m", "services.python_sandbox.main"]
