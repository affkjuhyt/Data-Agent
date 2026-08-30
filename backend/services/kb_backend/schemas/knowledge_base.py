"""Knowledge base schemas — request."""
from __future__ import annotations

from pydantic import BaseModel


class CreateKnowledgeBaseRequest(BaseModel):
    name: str
    description: str = ""
