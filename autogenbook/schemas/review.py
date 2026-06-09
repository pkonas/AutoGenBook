from __future__ import annotations

from typing import Literal, Optional

from pydantic import StrictStr, conint

from .base import StrictBaseModel
from .common import Verdict


class Scores(StrictBaseModel):
    novelty: conint(ge=1, le=10)
    technical_quality: conint(ge=1, le=10)
    clarity: conint(ge=1, le=10)
    evidence_grounding: conint(ge=1, le=10)
    reproducibility: conint(ge=1, le=10)


class MajorIssue(StrictBaseModel):
    issue: StrictStr
    why_it_matters: StrictStr
    where: Optional[StrictStr] = None
    required_change: StrictStr
    severity: Literal["major", "minor"]


class SuggestedRevision(StrictBaseModel):
    target: StrictStr
    instruction: StrictStr
    priority: conint(ge=1, le=5)


class ReviewAgentOutput(StrictBaseModel):
    overall_summary: StrictStr
    scores: Scores
    strengths: list[StrictStr]
    weaknesses: list[StrictStr]
    major_issues: list[MajorIssue]
    missing_experiments: list[StrictStr]
    citation_problems: list[StrictStr]
    suggested_revisions: list[SuggestedRevision]
    verdict: Verdict
