# syntax=docker/dockerfile:1
FROM python:3.11.9-slim

# tabulate: DataFrame.to_markdown(), which is how a result is rendered for a
# model to read (workflow_runtime's text_to_pandas renders inside this
# container now, not in its own process). openpyxl: .xlsx, which that tool has
# always claimed to accept.
RUN --mount=type=cache,target=/root/.cache/pip pip install pandas numpy plotly matplotlib tabulate openpyxl

RUN addgroup --system sandbox && adduser --system --ingroup sandbox sandbox
USER sandbox
WORKDIR /tmp

# No CMD/ENTRYPOINT — docker_runner.py always overrides the command with
# ["python3", "-c", code] per invocation; this image is never run as-is.
