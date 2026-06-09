from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictStr, conint, model_validator

from .base import StrictBaseModel
from .common import NodeKeyStr, RidStr


class ProblemStatement(StrictBaseModel):
    text: StrictStr
    evidence_rids: list[RidStr]


class Hypothesis(StrictBaseModel):
    text: StrictStr
    evidence_rids: list[RidStr]


class MethodProposal(StrictBaseModel):
    name: StrictStr
    high_level_description: StrictStr
    algorithm_sketch: list[StrictStr] = Field(min_length=1)
    expected_failure_modes: list[StrictStr]
    evidence_rids: list[RidStr]


class ExperimentMatrixItem(StrictBaseModel):
    exp_id: StrictStr = Field(pattern=r"^E\d+$")
    purpose: StrictStr
    conditions: dict[str, Any]
    metrics: list[StrictStr] = Field(min_length=1)
    success_criteria: StrictStr
    runtime_budget_minutes: conint(ge=0, le=10_000)


class AblationItem(StrictBaseModel):
    ablation_id: StrictStr = Field(pattern=r"^A\d+$")
    what_removed: StrictStr
    expected_effect: StrictStr
    metrics: list[StrictStr]


class PaperOutlineNode(StrictBaseModel):
    key: NodeKeyStr
    title: StrictStr
    summary: StrictStr


class PaperOutlineGraph(StrictBaseModel):
    root: Literal["paper"]
    nodes: list[PaperOutlineNode] = Field(min_length=2)
    edges: list[tuple[NodeKeyStr, NodeKeyStr]] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_edges(self) -> "PaperOutlineGraph":
        node_keys = {node.key for node in self.nodes}
        if self.root not in node_keys:
            raise ValueError("Root node is missing from nodes.")
        for src, dst in self.edges:
            if src not in node_keys or dst not in node_keys:
                raise ValueError("Edge references missing node key.")
        return self


class PlanAgentOutput(StrictBaseModel):
    selected_idea_id: StrictStr
    title: StrictStr
    problem_statement: ProblemStatement
    hypothesis: Hypothesis
    method_proposal: MethodProposal
    experiment_matrix: list[ExperimentMatrixItem] = Field(min_length=1)
    ablations: list[AblationItem]
    stopping_rules: list[StrictStr] = Field(min_length=1)
    paper_outline_graph: PaperOutlineGraph
    retrieval_queries: list[StrictStr]
