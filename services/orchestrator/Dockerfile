FROM python:3.11-slim
WORKDIR /app
COPY services/orchestrator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY libs/ ./libs/
COPY services/orchestrator/ .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
