# Build context is ../services (set in compose) so shared/ is available.
FROM python:3.12-slim
WORKDIR /app
COPY audit-bridge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ ./shared/
COPY audit-bridge/main.py .
CMD ["python", "-u", "main.py"]
