from typing import Optional

from pydantic import BaseModel


class APIKeyCreateInput(BaseModel):
    name: str
    scope: str = "read_only"
    expires_at: Optional[str] = None
