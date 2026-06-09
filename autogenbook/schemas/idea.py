from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictStr, conint, confloat

from .base import StrictBaseModel


class MinimalExperiment(StrictBaseModel):
    design: StrictStr
    expected_signal: StrictStr
    metric: StrictStr
    success_criteria: StrictStr


class EstimatedEffort(StrictBaseModel):
    time_minutes: conint(ge=0, le=10_000)
    complexity: Literal["low", "medium", "high"]


class Idea(StrictBaseModel):
    idea_id: StrictStr = Field(pattern=r"^I\d+$")
    title: StrictStr
    one_liner: StrictStr
    hypothesis: StrictStr
    core_mechanism: StrictStr
    what_is_new: StrictStr
    why_it_might_work: StrictStr
    minimal_experiment: MinimalExperiment
    ablations: list[StrictStr]
    risks: list[StrictStr]
    safety_ethics: list[StrictStr]
    estimated_effort: EstimatedEffort
    retrieval_queries: list[StrictStr]


class SelectionRubric(StrictBaseModel):
    novelty_weight: confloat(ge=0, le=1)
    testability_weight: confloat(ge=0, le=1)
    impact_weight: confloat(ge=0, le=1)
    risk_weight: confloat(ge=0, le=1)


class IdeaAgentOutput(StrictBaseModel):
    ideas: list[Idea] = Field(min_length=1)
    selection_rubric: SelectionRubric
