FROM python:3.11.9-slim

RUN pip install --no-cache-dir pandas numpy plotly matplotlib

RUN addgroup --system sandbox && adduser --system --ingroup sandbox sandbox
USER sandbox
WORKDIR /tmp

# No CMD/ENTRYPOINT — docker_runner.py always overrides the command with
# ["python3", "-c", code] per invocation; this image is never run as-is.
