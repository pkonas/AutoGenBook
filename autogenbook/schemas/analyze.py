from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictStr, confloat

from .base import StrictBaseModel
from .common import Confidence, PlotType, RidStr


class Claim(StrictBaseModel):
    claim_id: StrictStr = Field(pattern=r"^C\d+$")
    text: StrictStr
    type: Literal["result", "comparison", "observation", "limitation"]
    evidence_rids: list[RidStr] = Field(min_length=1)
    confidence: Confidence


class Comparison(StrictBaseModel):
    baseline: StrictStr
    proposed: StrictStr
    metric: StrictStr
    baseline_value: confloat(allow_inf_nan=False)
    proposed_value: confloat(allow_inf_nan=False)
    delta: confloat(allow_inf_nan=False)
    evidence_rids: list[RidStr]


class FigurePlanItem(StrictBaseModel):
    figure_id: StrictStr = Field(pattern=r"^F\d+$")
    filename: StrictStr
    plot_type: PlotType
    data_source: StrictStr
    caption: StrictStr
    evidence_rids: list[RidStr]


class LatexTableSnippet(StrictBaseModel):
    table_id: StrictStr = Field(pattern=r"^T\d+$")
    caption: StrictStr
    latex: StrictStr
    evidence_rids: list[RidStr]


class LimitationItem(StrictBaseModel):
    text: StrictStr
    evidence_rids: list[RidStr]
    severity: Literal["low", "medium", "high"]


class AnalyzeAgentOutput(StrictBaseModel):
    summary: StrictStr
    claims: list[Claim] = Field(min_length=1)
    comparisons: list[Comparison]
    figure_plan: list[FigurePlanItem]
    latex_table_snippets: list[LatexTableSnippet]
    limitations: list[LimitationItem]
    next_actions: list[StrictStr]
    retrieval_queries: list[StrictStr]
