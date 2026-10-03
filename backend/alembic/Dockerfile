# syntax=docker/dockerfile:1
FROM python:3.11-slim
ENV PYTHONPATH=/app
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/pip pip install sqlalchemy[asyncio] asyncpg alembic pydantic
# The migrations and the ORM they autogenerate against (env.py imports only
# libs.basemodel), not all of backend/: copying it all rebuilt this image for every
# service edit.
COPY backend/alembic/ ./alembic/
COPY backend/libs/__init__.py ./libs/__init__.py
COPY backend/libs/basemodel/ ./libs/basemodel/
COPY backend/libs/shared/__init__.py backend/libs/shared/tenant_rls.py backend/libs/shared/sql_identifiers.py ./libs/shared/

