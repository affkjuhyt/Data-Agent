FROM python:3.11-slim
ENV PYTHONPATH=/app
WORKDIR /app
RUN pip install --no-cache-dir sqlalchemy[asyncio] asyncpg alembic pydantic
COPY backend/ .
CMD ["alembic", "-c", "alembic/alembic.ini", "upgrade", "head"]
