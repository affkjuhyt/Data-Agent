"""The ingest_profile a Data Space, a document collection and a crawl source take (GEN-11).

One body model for the three, so the OpenAPI (and the frontend's generated types) name its keys;
the rules are libs/tabular.profile's, the ones the workers resolve with. Sending it replaces the
stored profile, `{}` clears it: a key left out is inherited from the level above, so a merge
could never take one back.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from libs.tabular import profile


class IngestProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: str | None = Field(default=None, max_length=35, examples=["vi-VN", "en-US"])
    decimal_mark: Literal[".", ","] | None = None
    date_order: Literal["dmy", "mdy"] | None = None
    ocr_languages: list[str] | None = Field(default=None, min_length=1, max_length=5)

    @model_validator(mode="after")
    def _known(self) -> IngestProfile:
        profile.validate(self.stored())
        return self

    def stored(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


def stored(value: IngestProfile | None) -> dict[str, Any]:
    return value.stored() if value is not None else {}
