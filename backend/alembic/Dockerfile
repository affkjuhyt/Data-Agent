# syntax=docker/dockerfile:1
FROM python:3.11-slim
ENV PYTHONPATH=/app
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/pip pip install "sqlalchemy" "psycopg[binary]" alembic
# The revisions apply the generated SQL in alembic/sql (backend/schema/csdl/gen_ddl.py); they
# import nothing from libs, so the image holds only backend/alembic.
COPY backend/alembic/ ./alembic/
