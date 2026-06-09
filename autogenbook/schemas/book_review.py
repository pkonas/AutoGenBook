from __future__ import annotations

from typing import Literal

from pydantic import StrictStr

from .base import StrictBaseModel


class Issue(StrictBaseModel):
    type: Literal["grounding", "consistency", "latex", "clarity", "pedagogy"]
    severity: Literal["major", "minor"]
    description: StrictStr
    required_fix: StrictStr


class SuggestedEdit(StrictBaseModel):
    target: StrictStr
    edit_instruction: StrictStr


class BookSectionReviewerOutput(StrictBaseModel):
    ok_to_keep: bool
    issues: list[Issue]
    suggested_edits: list[SuggestedEdit]
    retrieval_queries: list[StrictStr]
