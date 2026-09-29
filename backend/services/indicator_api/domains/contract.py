"""Base class and time axes shared by every contract model."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


def _stamp_schema_version(schema: dict[str, Any], model: type[BaseModel]) -> None:
    # The committed openapi.json is the agreed definition (P0-09 C8); the
    # contract test compares each schema against it and needs to know whether
    # a change came with a version bump, so the version travels in the schema.
    schema["x-schema-version"] = getattr(model, "SCHEMA_VERSION", 1)


class ContractModel(BaseModel):
    """A business definition. Bump SCHEMA_VERSION whenever a field changes.

    `extra="forbid"`: a producer may not add fields outside the shared
    definition (P0-09 section 3) — an unknown field is refused, not dropped.
    """

    model_config = ConfigDict(extra="forbid", json_schema_extra=_stamp_schema_version)

    SCHEMA_VERSION: ClassVar[int] = 1


class TimeAxes(ContractModel):
    """The time axes every observation, claim and event carries (03 section 3.1).

    Every instant must carry its offset: a naive timestamp read as UTC on one
    host and as local time on another shifts `available_at`, and point-in-time
    answers (invariant 9) then see data published after the as-of date.
    """

    event_time_start: AwareDatetime
    event_time_end: AwareDatetime
    market_timezone: str = Field(
        min_length=1, description="IANA zone the value is fixed in, e.g. America/New_York"
    )
    publication_time: AwareDatetime
    available_at: AwareDatetime
    system_time: AwareDatetime
