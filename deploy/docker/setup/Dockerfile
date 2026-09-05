# bash/curl baked in at build time — the setup Job's pod has no egress to
# the internet (NetworkPolicy only allows the dataagent-* namespaces + DNS +
# the remote LLM IP), so `apk add` at container runtime always fails.
FROM python:3.12-alpine
RUN apk add --no-cache bash curl
