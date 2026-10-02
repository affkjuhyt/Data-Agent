# syntax=docker/dockerfile:1
# A service image plus the test tooling, built once and cached. `make test-unit` /
# `make test-integration` used to `pip install` requirements-integration.txt into a
# fresh container on every run of every suite — about 15 s each, often longer than
# the tests. Rebuilt automatically when the service image or a requirements file
# changes (both are inputs of the pip layer); otherwise the build is a cache hit.
ARG BASE
FROM ${BASE}
ARG SUITE
USER root
COPY requirements-test.txt requirements-integration.txt /tmp/test-reqs/
COPY tests/integration/ /tmp/test-reqs/integration/
RUN --mount=type=cache,target=/root/.cache/pip \
    extra="/tmp/test-reqs/integration/${SUITE}/requirements.txt"; \
    pip install -q -r /tmp/test-reqs/requirements-integration.txt $( [ -f "$extra" ] && echo "-r $extra" )
