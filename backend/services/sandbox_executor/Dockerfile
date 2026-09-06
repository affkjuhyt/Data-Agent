# SECURITY NOTE: this image intentionally stays root. sandbox_executor spawns
# sandbox containers via the mounted Docker socket (docker_runner.py); a non-root
# user would need to join the host's docker group (GID varies per host) which is
# itself root-equivalent. Proper hardening is rootless Docker / gVisor / a
# dedicated node pool — tracked separately, not solvable with a plain USER line.
FROM python:3.11.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Every module, not a file list. The list version silently went stale twice:
# clickhouse_readonly.py was added as a third SQL backend and never copied, so
# the image kept building and failed only on the first ClickHouse query.
# __pycache__ is excluded by the repo .dockerignore.
COPY *.py ./
COPY api ./api
COPY core ./core
COPY execution ./execution

# No CMD/EXPOSE: the start command and the port are deployment concerns and
# live in deploy/k8s. Keeping them here too meant two places to change.
