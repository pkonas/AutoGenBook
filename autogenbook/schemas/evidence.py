from __future__ import annotations

from typing import Optional

from pydantic import Field, StrictStr

from .base import StrictBaseModel
from .common import CiteKeyStr, RidStr


class EvidenceRef(StrictBaseModel):
    rid: RidStr
    cite_key: Optional[CiteKeyStr] = None


class ClaimEvidence(StrictBaseModel):
    evidence_rids: list[RidStr] = Field(default_factory=list)


class CitationProblem(StrictBaseModel):
    description: StrictStr
    where: Optional[StrictStr] = None
