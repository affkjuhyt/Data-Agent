FROM python:3.12-alpine

WORKDIR /app

RUN apk add --no-cache gcc musl-dev curl

COPY minio-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY minio-service/main.py .

EXPOSE 8400

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8400"]
