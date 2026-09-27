# syntax=docker/dockerfile:1
# SECURITY NOTE: this image intentionally stays root for the SANDBOX_BACKEND=
# docker path (default, docker-compose deployments) — sandbox_executor spawns
# sandbox containers via the mounted Docker socket (docker_runner.py), and a
# non-root user would need to join the host's docker group (GID varies per
# host) which is itself root-equivalent. Proper hardening for that path is
# rootless Docker / gVisor / a dedicated node pool — tracked separately, not
# solvable with a plain USER line.
#
# SANDBOX_BACKEND=kubernetes (deploy/k8s/) needs none of that: it calls the
# API server over HTTPS with its own ServiceAccount token (k8s_runner.py),
# an ordinary unprivileged operation — deploy/k8s/production/service/sandbox-executor.yaml
# runs this same image as runAsNonRoot/runAsUser 10001 there, which k8s sets
# directly regardless of this image's own (root) default.
FROM python:3.11.9-slim
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
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
