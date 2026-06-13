FROM python:3.13-slim

WORKDIR /app

ENV PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/lists/*

COPY services/backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY services/ ./services/
COPY basemodel/ ./basemodel/
COPY alembic/ ./alembic/
COPY alembic/alembic.ini ./

EXPOSE 8000

CMD ["uvicorn", "services.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
