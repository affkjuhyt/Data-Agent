"""Settings, all from the environment (deploy-time values from Ansible, secrets
from OpenBao through the deploy; nothing here has a production default)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from services.api.kernel.db import role_urls_from_env


@dataclass(frozen=True)
class Settings:
    # The realm's issuer URL as it appears in the token's `iss`, e.g.
    # https://sso.example/realms/aeroflow.
    oidc_issuer: str = ""
    oidc_audience: str = "dataagent-api"
    # Defaults to the issuer's certs endpoint; set when the api reaches
    # Keycloak by an in-cluster address different from the public issuer.
    oidc_jwks_url: str | None = None
    db_role_urls: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ
        return cls(
            oidc_issuer=env.get("API_OIDC_ISSUER", ""),
            oidc_audience=env.get("API_OIDC_AUDIENCE", "dataagent-api"),
            oidc_jwks_url=env.get("API_OIDC_JWKS_URL") or None,
            db_role_urls=role_urls_from_env(env),
        )
