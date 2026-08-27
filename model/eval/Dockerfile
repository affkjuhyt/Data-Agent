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
COPY services/workflow-runtime/requirements.txt ./runtime-requirements.txt
# ragas + judge model client, then the runtime's own deps (litellm, pydantic)
# so the intent scorer can call the planner node itself.
RUN pip install --no-cache-dir -r ragas-requirements.txt \
    && pip install --no-cache-dir -r runtime-requirements.txt \
    && pip install --no-cache-dir sqlparse

COPY model ./model
COPY libs ./libs
COPY services/sandbox-executor ./services/sandbox-executor
COPY services/workflow-runtime ./services/workflow-runtime

# Runs everything and gates on the result: a report with no verdict is a
# dashboard, and this container exists to answer yes or no.
CMD ["sh", "-c", "python -m model.eval.run --tier all --out /reports/eval-report.json && python -m model.eval.gate --report /reports/eval-report.json"]
