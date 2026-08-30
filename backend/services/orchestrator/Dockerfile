FROM python:3.11-slim
WORKDIR /app
COPY backend/services/orchestrator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/libs/ ./libs/
COPY backend/services/orchestrator/ .
# Drop root — read-only app surface.
RUN addgroup --system app && adduser --system --ingroup app app
USER app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
