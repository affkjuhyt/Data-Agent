"""The static execution-plan contract shared by planners and executors (V3 R0-10)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from libs.contracts.v1.base import ContractModel, NonEmptyStr
from libs.contracts.v1.result_set import ResultColumn

PortType = Literal["result_set", "evidence_list", "text", "chart_spec", "report", "scalar"]
PlanNodeKind = Literal[
    "SQL_QUERY",
    "RETRIEVE_DOC",
    "EXTRACT_EVIDENCE",
    "COMPUTE",
    "VALIDATE",
    "SYNTHESIZE_REPORT",
    "CLARIFY",
    "HUMAN_APPROVAL",
    "AGENT",
]
EdgeKind = Literal["HARD_DEP", "SOFT_DEP", "ALT_GROUP"]
TrustKind = Literal["VERIFIED", "GOVERNED", "EXPLORATORY", "DOCUMENT"]
OutputShape = Literal["table", "scalar", "series", "report", "text"]


class Port(ContractModel):
    key: NonEmptyStr
    type: PortType
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")


class OutputContract(ContractModel):
    shape: OutputShape
    columns: list[ResultColumn] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    precision: dict[str, int] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)


class PlanNode(ContractModel):
    id: NonEmptyStr
    kind: PlanNodeKind
    capability: NonEmptyStr
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: list[Port] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    evidence_requirements: list[dict[str, Any]] = Field(default_factory=list)
    trust: TrustKind | None = None


class PlanEdge(ContractModel):
    from_: NonEmptyStr = Field(alias="from")
    to: NonEmptyStr
    kind: EdgeKind = "HARD_DEP"


class PlanIR(ContractModel):
    plan_id: NonEmptyStr
    output_contract: OutputContract
    nodes: list[PlanNode] = Field(default_factory=list)
    edges: list[PlanEdge] = Field(default_factory=list)
    budget: dict[str, int] = Field(default_factory=dict)
