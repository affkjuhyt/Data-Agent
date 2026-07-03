FROM python:3.11.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py security_visitor.py docker_runner.py sql_guard.py output.py snowflake_readonly.py ./
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8500"]
