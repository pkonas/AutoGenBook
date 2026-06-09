from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictStr, model_validator

from .base import StrictBaseModel


class ExpectedEffect(StrictBaseModel):
    metric: StrictStr
    direction: Literal["increase", "decrease", "stabilize"]
    mechanism: StrictStr


class CodePatchAgentOutput(StrictBaseModel):
    patch_format: Literal["unified_diff"]
    target_files: list[StrictStr] = Field(default_factory=list)
    diff: StrictStr
    rationale: list[StrictStr] = Field(default_factory=list)
    expected_effect: ExpectedEffect
    safety_notes: list[StrictStr] = Field(default_factory=list)
    rollback_plan: StrictStr
    retrieval_queries: list[StrictStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_diff_targets(self) -> "CodePatchAgentOutput":
        diff_text = self.diff.strip()
        if not diff_text:
            if self.target_files and not self.rationale:
                raise ValueError("Empty diff requires empty target_files or rationale explaining no change.")
        else:
            if not self.target_files:
                raise ValueError("Non-empty diff requires target_files.")
        return self
