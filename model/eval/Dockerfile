# Image for the ONLINE evaluation tier — the one that needs a model.
#
# The offline tier deliberately does not need this: it is pure Python over the
# real guards and the real metric compiler, so CI installs sqlparse and runs
# `python -m model.eval.run --tier offline` directly. Building a 1GB image to
# gate a pull request would be the reason nobody gates pull requests.
#
# The service trees are copied rather than pip-installed because the scorers
# import the REAL modules — sql_guard, security_visitor, the planner node, the
# shipped responder prompt. An eval that vendored its own copies would grade
# the copies.
FROM python:3.11-slim

WORKDIR /app

COPY model/ragas/requirements.txt ./ragas-requirements.txt
COPY backend/services/workflow_runtime/requirements.txt ./runtime-requirements.txt
# ragas + judge model client, then the runtime's own deps (litellm, pydantic)
# so the intent scorer can call the planner node itself.
RUN pip install --no-cache-dir -r ragas-requirements.txt \
    && pip install --no-cache-dir -r runtime-requirements.txt \
    && pip install --no-cache-dir sqlparse

# Same layout as the checkout (backend/…) so model/eval/_paths.py resolves
# the service trees identically in CI and in this image.
COPY model ./model
COPY backend/libs ./backend/libs
COPY backend/services/sandbox_executor ./backend/services/sandbox_executor
COPY backend/services/workflow_runtime ./backend/services/workflow_runtime

# Runs everything and gates on the result: a report with no verdict is a
# dashboard, and this container exists to answer yes or no.