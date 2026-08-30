from pydantic import BaseModel


class IPAllowlistCreateInput(BaseModel):
    cidr: str
    label: str = ""
    is_active: bool = True
