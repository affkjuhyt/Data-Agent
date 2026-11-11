"""The api's OpenBao, from its own AppRole (OPENBAO_ROLE_ID, OPENBAO_SECRET_ID): libs/openbao.

The api writes a source account typed in the UI, reads a model provider's key and keeps the exec
vault's Transit keys. Its AppRole policy (dataagent-api-policy, deploy/ansible/roles/k8s_openbao)
grants exactly that; the api used to hold the root token for it. One client per process, so the
login is shared by every call until a little before its lease ends.

Without an AppRole the static OPENBAO_TOKEN is used, which is what a test or a local run sets; a
cluster never gives the api one.
"""

from __future__ import annotations

import functools

from libs.openbao import OpenBao, OpenBaoError, from_env

__all__ = ["OpenBao", "OpenBaoError", "forget", "vault"]


@functools.cache
def vault() -> OpenBao | None:
    """The api's OpenBao client, or None when OpenBao is not configured at all."""
    return from_env()


def forget() -> None:
    """Drop the client and its login (tests; a configuration that changed)."""
    vault.cache_clear()
