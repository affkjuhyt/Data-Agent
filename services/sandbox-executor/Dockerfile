# SECURITY NOTE: this image intentionally stays root. sandbox-executor spawns
# sandbox containers via the mounted Docker socket (docker_runner.py); a non-root
# user would need to join the host's docker group (GID varies per host) which is
# itself root-equivalent. Proper hardening is rootless Docker / gVisor / a
# dedicated node pool — tracked separately, not solvable with a plain USER line.
FROM python:3.11.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py security_visitor.py docker_runner.py pool.py job_queue.py minio_store.py sql_guard.py output.py snowflake_readonly.py postgres_readonly.py ./
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8500"]
