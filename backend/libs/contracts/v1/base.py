"""Base class and shared field types for every contract v1 model.

The contract is what two branches agree on without importing each other, so it
is strict on both sides of the wire: an unknown field is refused, not dropped,
and each model states the version of its own shape.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, ClassVar

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
)


def _stamp_schema_version(schema: dict[str, Any], model: type[BaseModel]) -> None:
    # The version travels inside the published schema so a compatibility check
    # (NOP-152) can tell "changed with a bump" from "changed without one" by
    # comparing two schema files alone, without importing either side's code.
    schema["x-schema-version"] = getattr(model, "SCHEMA_VERSION", 1)


class ContractModel(BaseModel):
    """A wire shape shared across services. Bump SCHEMA_VERSION on any breaking change.

    What counts as breaking is in libs/contracts/COMPATIBILITY.md.
    """

    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, json_schema_extra=_stamp_schema_version
    )

    SCHEMA_VERSION: ClassVar[int] = 1

    # The wire key `from` (release_info, mapping time) is a Python keyword, so
    # the field is `from_` with an alias. Dumping by alias by default keeps the
    # wire spelling for every caller; pydantic 2.9 (pinned in the images) has no
    # `serialize_by_alias` setting yet.
    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("by_alias", True)
        dumped: dict[str, Any] = super().model_dump(**kwargs)
        return dumped

    def model_dump_json(self, **kwargs: Any) -> str:
        kwargs.setdefault("by_alias", True)
        dumped: str = super().model_dump_json(**kwargs)
        return dumped


def _refuse_float(value: object) -> object:
    # By the time a float arrives its digits are already gone (25760.1 is not
    # 25760.1 in binary), so it is refused rather than converted — the same rule
    # libs.indicator.quantity.Quantity enforces. bool is an int subclass and is
    # never a measurement.
    if isinstance(value, float | bool):
        raise ValueError("a decimal value must be sent as a string or integer, never a float")
    return value


DecimalValue = Annotated[
    Decimal,
    BeforeValidator(_refuse_float),
    PlainSerializer(lambda v: str(v), return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": r"^-?\d+(\.\d+)?$"}),
]
"""A number on the wire is a decimal string ("25760.00"), so no JSON parser turns it into a float."""

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

# Checked by shape, not against zoneinfo: slim images and Windows hosts may ship
# without a tz database, and a contract that validates differently per host is
# not a contract. The consumer that converts times owns the real lookup.
IanaTimezone = Annotated[
    str,
    Field(
        pattern=r"^(UTC|[A-Z][A-Za-z_]+(/[A-Za-z0-9_+\-]+)+)$",
        description="IANA zone, e.g. America/New_York, Asia/Ho_Chi_Minh, UTC",
    ),
]

ClockTime = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM, 24h")]

NonEmptyStr = Annotated[str, Field(min_length=1)]


class TimeAxes(ContractModel):
    """The four time axes every number carries (00 invariant 8, 14 IN-5, IN-7).

    The period is `[event_time_start, event_time_end)`; an instant has start == end.
    Every instant carries its offset: a naive timestamp read as UTC on one host
    and local time on another shifts `available_at`, and an as-of answer
    (invariant 9) then sees data published after the as-of date.
    """

    event_time_start: AwareDatetime
    event_time_end: AwareDatetime
    market_timezone: IanaTimezone
    publication_time: AwareDatetime
    available_at: AwareDatetime
    system_time: AwareDatetime
