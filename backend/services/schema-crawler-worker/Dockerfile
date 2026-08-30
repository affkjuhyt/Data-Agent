# Build context is the repo root (set in compose) so libs/ is reachable — the
# governance vocabulary (classification labels, the FRESH/STALE/UNKNOWN rule) is
# shared with workflow-runtime, and a second copy of it here would drift into
# the crawler and the agent disagreeing about whether a table is stale.
FROM python:3.12-slim
WORKDIR /app
COPY backend/services/schema-crawler-worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# libs/ keeps its real path rather than being flattened to `shared/`
# (audit-consumer's convention): the tests import `libs.shared.freshness` from
# the repo root, and matching that here means one import statement works in both.
COPY backend/libs/__init__.py ./libs/__init__.py
COPY backend/libs/shared/__init__.py libs/shared/freshness.py ./libs/shared/
# Whole directory, not a file list. The list version silently went stale the
# moment this worker gained a second metadata engine — the image kept building
# and then failed at `import metadata`. __pycache__ and *.pyc are excluded by
# the repo .dockerignore.
COPY backend/services/schema-crawler-worker/ ./
# Drop root — read-only app surface.
RUN addgroup --system app && adduser --system --ingroup app app
USER app
CMD ["python", "-u", "main.py"]
