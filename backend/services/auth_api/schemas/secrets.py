from pydantic import BaseModel


class SecretCreateInput(BaseModel):
    key_name: str
    key_type: str
    algorithm: str = ""
    realm: str = ""
    value: str = ""
    rotation_days: int = 90


class SignPayload(BaseModel):
    data: str


class VerifyPayload(BaseModel):
    data: str
    signature: str
