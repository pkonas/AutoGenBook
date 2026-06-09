from __future__ import annotations

from typing import Any

from pydantic import Field, StrictStr, confloat, model_validator

from .base import StrictBaseModel


class SubNode(StrictBaseModel):
    title: StrictStr = Field(min_length=1)
    summary: StrictStr
    n_pages: confloat(gt=0, le=1000)
    needsSubdivision: bool

    @model_validator(mode="after")
    def _check_one_decimal(self) -> "SubNode":
        if round(self.n_pages, 1) != self.n_pages:
            raise ValueError("n_pages must have at most one decimal place.")
        return self


class SubdivideOutput(StrictBaseModel):
    items: list[SubNode] = Field(min_length=1)


def validate_subdivide_array(arr: list[dict[str, Any]]) -> SubdivideOutput:
    return SubdivideOutput(items=arr)
