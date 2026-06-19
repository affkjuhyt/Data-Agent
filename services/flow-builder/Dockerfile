FROM python:3.11-slim
WORKDIR /app
COPY services/flow-builder/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY basemodel/ ./basemodel/
COPY services/database_connector/ ./services/database_connector/
COPY services/log_set_up/ ./services/log_set_up/
COPY services/shared/ ./services/shared/
COPY services/flow-builder/ .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
