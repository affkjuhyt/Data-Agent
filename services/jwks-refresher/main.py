"""
JWKS Auto-Refresh Service (300s TTL)

Implements §1.4 of the design doc: automatic JWKS key sync between
Keycloak and Kong every 300 seconds. Bridges the gap between Kong's
built-in JWT plugin (static key) and the designed OIDC plugin behavior
(jwks_uri_refresh_interval: 300).

On each cycle:
  1. Fetch the current signing key from Keycloak JWKS endpoint
  2. Compare kid with the last known kid
  3. If changed (key rotation detected): update Kong JWT credential
  4. Sleep 300s
"""
import base64
import json
import logging
import os
import time
import urllib.request

import requests
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from shared.atomic_io import atomic_write_text
from shared.observability import setup_observability

setup_observability("jwks-refresher")
log = logging.getLogger("jwks-refresher")

KONG_ADMIN = os.environ.get("KONG_ADMIN_URL", "http://kong:8001")
KC_BASE = os.environ.get("KEYCLOAK_URL", "http://keycloak-lb:8080")
REALM = os.environ.get("KEYCLOAK_REALM", "aeroflow")
CONSUMER = os.environ.get("KONG_CONSUMER", "keycloak-users")
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL_SECONDS", "300"))

JWKS_URL = f"{KC_BASE}/realms/{REALM}/protocol/openid-connect/certs"
ISSUER = f"{KC_BASE}/realms/{REALM}"

# State file persists _last_kid across restarts so we don't re-register on every boot.
# Mount the parent directory as a Docker volume (e.g. /data) to survive container restarts.
_STATE_FILE = os.environ.get("JWKS_STATE_FILE", "/data/last_kid.txt")


def _load_last_kid() -> str:
    try:
        return open(_STATE_FILE).read().strip()
    except Exception:
        return ""


def _save_last_kid(kid: str) -> None:
    try:
        atomic_write_text(_STATE_FILE, kid)
    except Exception as e:
        log.warning("Could not persist last_kid to %s: %s", _STATE_FILE, e)


def b64url_to_int(s: str) -> int:
    s += "=" * (-len(s) % 4)
    return int.from_bytes(base64.urlsafe_b64decode(s), "big")


def jwks_to_pem(jwks_url: str) -> tuple[str, str]:
    """Returns (kid, pem_public_key) for the most recently added signing key.

    Keycloak returns keys in ascending age order; the last sig key is the
    currently active one.  Passive/old keys are also present but shouldn't
    be used for new credential registration.  We register only one key here
    because Kong's JWT plugin maps issuer→credential 1-to-1; for full key
    overlap (serve tokens signed by older keys until their exp), switch to
    the aeroflow-jwks plugin which caches the entire JWKS and matches by kid.
    """
    with urllib.request.urlopen(jwks_url, timeout=10) as r:
        data = json.load(r)

    rsa_keys = [k for k in data["keys"] if k["kty"] == "RSA"]
    sig_keys = [k for k in rsa_keys if k.get("use") == "sig"]
    # Keycloak puts the active key last in the list.
    key = sig_keys[-1] if sig_keys else rsa_keys[-1]
    kid = key["kid"]

    n = b64url_to_int(key["n"])
    e = b64url_to_int(key["e"])

    # Build the public key from the JWK modulus/exponent and let cryptography
    # emit the SubjectPublicKeyInfo PEM. Hand-rolling DER/ASN.1 here is what the
    # previous implementation did — one wrong length byte produced a malformed
    # PEM that Kong silently rejected.
    public_key = RSAPublicNumbers(e=e, n=n).public_key()
    pem = public_key.public_bytes(
        encoding=Encoding.PEM,
        format=PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return kid, pem


def update_kong_credential(pem: str) -> None:
    # Snapshot existing IDs before creating the new credential so we can
    # delete only the old ones — tokens signed with them are still valid until
    # their exp claim, and Kong will reject them once the credential is removed.
    existing = requests.get(f"{KONG_ADMIN}/consumers/{CONSUMER}/jwt", timeout=10)
    existing.raise_for_status()
    old_ids = [c["id"] for c in existing.json().get("data", [])]

    # Create new credential first so there is no gap in Kong's keystore.
    resp = requests.post(
        f"{KONG_ADMIN}/consumers/{CONSUMER}/jwt",
        json={"algorithm": "RS256", "key": ISSUER, "rsa_public_key": pem},
        timeout=10,
    )
    resp.raise_for_status()
    new_id = resp.json().get("id", "?")
    log.info("Registered new Kong JWT credential %s", new_id)

    # Now remove the old credentials.  Tokens already issued with the old key
    # will stop being accepted immediately; ensure token TTLs are short enough
    # (recommended: ≤ jwks_refresh_interval) to avoid disruption.
    for cred_id in old_ids:
        try:
            requests.delete(
                f"{KONG_ADMIN}/consumers/{CONSUMER}/jwt/{cred_id}", timeout=10
            ).raise_for_status()
            log.info("Deleted old credential %s", cred_id)
        except Exception as e:
            log.warning("Could not delete old credential %s: %s", cred_id, e)


def refresh_once() -> None:
    last_kid = _load_last_kid()
    kid, pem = jwks_to_pem(JWKS_URL)

    if kid == last_kid:
        log.debug("JWKS kid unchanged (%s), no update needed", kid)
        return

    log.info("JWKS kid changed: %s → %s — updating Kong", last_kid or "(first run)", kid)
    update_kong_credential(pem)
    _save_last_kid(kid)
    log.info("Kong credential updated for kid=%s", kid)


def wait_for_services(retries: int = 30, delay: int = 10) -> None:
    for attempt in range(1, retries + 1):
        try:
            requests.get(f"{KONG_ADMIN}/status", timeout=5).raise_for_status()
            urllib.request.urlopen(JWKS_URL, timeout=5)
            log.info("Kong and Keycloak are reachable")
            return
        except Exception:
            log.warning("Services not ready, attempt %d/%d, retry in %ds", attempt, retries, delay)
            time.sleep(delay)
    raise RuntimeError("Services unavailable after retries")


def main():
    log.info(
        "Starting JWKS refresher — interval=%ds, kong=%s, keycloak=%s",
        REFRESH_INTERVAL,
        KONG_ADMIN,
        KC_BASE,
    )
    wait_for_services()

    while True:
        try:
            refresh_once()
        except Exception as e:
            log.error("Refresh failed: %s", e)
        time.sleep(REFRESH_INTERVAL)


if __name__ == "__main__":
    main()
