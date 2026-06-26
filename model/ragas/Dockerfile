FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default: run evaluation in dataset mode
CMD ["python", "eval.py", "--mode", "dataset"]
