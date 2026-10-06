import os

import hvac

from services.auth_api.infrastructure.config import OPENBAO_ADDR

TOKEN_FILE = "/openbao/data/.root-token"


def load_openbao_token() -> str:
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE) as token_file:
                token = token_file.read().strip()
            if token:
                return token
        except OSError:
            pass
    return os.environ.get("OPENBAO_TOKEN", "aeroflow-dev-token")


OPENBAO_TOKEN = load_openbao_token()


def get_vault() -> hvac.Client:
    return hvac.Client(url=OPENBAO_ADDR, token=load_openbao_token())
