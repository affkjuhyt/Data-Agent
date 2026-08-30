"""Mutual-TLS client context for outbound service-to-service calls.

deploy/docker/certs/gen-certs.sh already issues a CA, a server certificate for the
backend and client certificates with the `clientAuth` EKU — the scheme was
designed for Kong -> backend mTLS and never wired to anything. This module is
the client half for workflow-runtime -> kb-backend.

Two independent layers guard that call and they answer different questions:

  * mutual TLS (here) — "is the peer a holder of a certificate our CA signed,
    and is the server the one we expect?" Authenticates the *connection*.
  * the signed assertion (backend/libs/shared/service_identity.py) — "which service is
    calling, on whose behalf, and has the claim expired?" Authenticates the
    *request*.

mTLS alone would let any CA-signed client assert any identity; the assertion
alone is only as good as the network. Enabling both is the intended posture,
and each is independently useful, so both are opt-in via env rather than one
depending on the other.

Unset env => returns True, i.e. httpx's ordinary verification. That keeps
existing plain-HTTP stacks working unchanged; `http://` endpoints ignore the
value entirely.
"""
from __future__ import annotations

import functools
import logging
import os
import ssl

logger = logging.getLogger(__name__)

# Client certificate this service presents. Both must be set for mTLS.
MTLS_CLIENT_CERT_ENV = "MTLS_CLIENT_CERT"
MTLS_CLIENT_KEY_ENV = "MTLS_CLIENT_KEY"
# CA used to verify the peer's server certificate. Optional: without it the
# system trust store is used, which is right for a publicly-issued cert and
# wrong for the self-signed CA gen-certs.sh produces.
MTLS_CA_CERT_ENV = "MTLS_CA_CERT"


@functools.lru_cache(maxsize=1)
def kb_backend_ssl_context() -> ssl.SSLContext | bool:
    """SSLContext carrying this service's client certificate, or True.

    Cached: building a context parses the certificate files, and kb_search runs
    on every agent turn. A cert change therefore needs a restart, which matches
    how the rest of this service reads its configuration.
    """
    cert = os.environ.get(MTLS_CLIENT_CERT_ENV, "")
    key = os.environ.get(MTLS_CLIENT_KEY_ENV, "")
    ca = os.environ.get(MTLS_CA_CERT_ENV, "")

    if not cert and not key:
        if ca:
            logger.info("mtls | %s set without a client cert — verifying the peer only", MTLS_CA_CERT_ENV)
            return ssl.create_default_context(cafile=ca)
        return True

    if not (cert and key):
        # Half-configured is a deployment mistake, and silently downgrading to
        # no client certificate would hide it until an audit.
        raise RuntimeError(
            f"mTLS is half-configured: set both {MTLS_CLIENT_CERT_ENV} and {MTLS_CLIENT_KEY_ENV}, or neither"
        )

    context = ssl.create_default_context(cafile=ca or None)
    context.load_cert_chain(certfile=cert, keyfile=key)
    logger.info("mtls | client certificate loaded from %s (ca=%s)", cert, ca or "system trust store")
    return context
