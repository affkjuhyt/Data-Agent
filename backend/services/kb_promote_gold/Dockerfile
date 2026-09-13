FROM python:3.11-slim

WORKDIR /app

COPY backend/services/kb_promote_gold/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY backend/services/__init__.py   /app/services/__init__.py
COPY backend/libs/                  /app/libs/
COPY backend/services/kb_promote_gold/         /app/services/kb_promote_gold/

ENV PYTHONPATH=/app

RUN addgroup --system app && adduser --system --ingroup app --home /home/app app     && chown -R app:app /home/app
USER app
