# syntax=docker/dockerfile:1
FROM python:3.11-slim
ENV PYTHONPATH=/app
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/pip pip install sqlalchemy[asyncio] asyncpg alembic pydantic
COPY backend/ .

