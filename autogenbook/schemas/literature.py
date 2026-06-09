from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, StrictStr

from .base import StrictBaseModel
from .common import CiteKeyStr, RidStr


class RelatedWorkItem(StrictBaseModel):
    cite_key: CiteKeyStr
    title: StrictStr
    year: Optional[StrictStr] = None
    key_takeaways: list[StrictStr] = Field(min_length=1)
    closest_overlap: StrictStr
    difference: StrictStr
    evidence_rids: list[RidStr] = Field(min_length=1)


class NoveltyRisk(StrictBaseModel):
    level: Literal["low", "medium", "high"]
    reasons: list[StrictStr]
    confounders: list[StrictStr]
    pivot_suggestions: list[StrictStr]


class PositioningStatement(StrictBaseModel):
    problem_gap: StrictStr
    our_angle: StrictStr
    why_now: Optional[StrictStr] = None
    evidence_rids: list[RidStr] = Field(default_factory=list)


class LiteratureAgentOutput(StrictBaseModel):
    related_work: list[RelatedWorkItem]
    novelty_risk: NoveltyRisk
    positioning_statement: PositioningStatement
    must_cite: list[CiteKeyStr]
    retrieval_queries: list[StrictStr]
