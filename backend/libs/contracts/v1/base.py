"""Base class and shared field types of the contract v1 models.

What two sides agree on without importing each other is strict on both sides of the wire: an
unknown field is refused, not dropped, and each model states the version of its own shape.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """A wire shape shared across services. Bump SCHEMA_VERSION on any breaking change: a
    field removed, renamed or narrowed, or a new required one."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    SCHEMA_VERSION: ClassVar[int] = 1

    # A wire key that is a Python keyword is a field with an alias; dumping by alias by default
    # keeps the wire spelling for every caller (pydantic 2.9, pinned in the images, has no
    # `serialize_by_alias` setting yet).
    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("by_alias", True)
        dumped: dict[str, Any] = super().model_dump(**kwargs)
        return dumped

    def model_dump_json(self, **kwargs: Any) -> str:
        kwargs.setdefault("by_alias", True)
        dumped: str = super().model_dump_json(**kwargs)
        return dumped


NonEmptyStr = Annotated[str, Field(min_length=1)]
