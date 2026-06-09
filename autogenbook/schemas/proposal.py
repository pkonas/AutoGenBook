from __future__ import annotations

from typing import Optional, Literal

from pydantic import Field, StrictStr, conint
from pydantic.types import Annotated

from .base import StrictBaseModel
from .common import RidStr


ReqIdStr = Annotated[str, Field(pattern=r"^REQ-[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")]
SecIdStr = Annotated[str, Field(pattern=r"^SEC-[0-9]{3}(?:\.[0-9]+)*$")]
AnnexIdStr = Annotated[str, Field(pattern=r"^ANN-[0-9]{3}$")]
QuestionIdStr = Annotated[str, Field(pattern=r"^Q-[0-9]{3}$")]
SourceIdStr = Annotated[str, Field(pattern=r"^SRC[:\\-][A-Za-z0-9_-]{4,64}$")]


class KB1Evidence(StrictBaseModel):
    source_id: StrictStr
    file: StrictStr
    chunk_id: StrictStr
    quote: StrictStr


class LLM1Requirement(StrictBaseModel):
    id: ReqIdStr
    title: StrictStr
    type: Literal[
        "section",
        "format",
        "evaluation",
        "legal",
        "ethics",
        "budget",
        "eligibility",
        "annex",
        "other",
    ]
    requirement_text: StrictStr
    priority: Literal["must", "should", "may"]
    evidence: list[KB1Evidence] = Field(min_length=1)


class FormatLimits(StrictBaseModel):
    page_limit: Optional[conint(ge=0)] = None
    word_limit: Optional[conint(ge=0)] = None
    char_limit: Optional[conint(ge=0)] = None


class FormatSpec(StrictBaseModel):
    required_output_formats: list[StrictStr] = Field(min_length=1)
    citation_standard: StrictStr
    language: StrictStr
    limits: FormatLimits


class UnknownOrAmbiguous(StrictBaseModel):
    topic: StrictStr
    why_unknown: StrictStr
    what_to_look_for_in_kb1: StrictStr


class ProposalLLM1Output(StrictBaseModel):
    language: StrictStr
    kb1_requirement_digest: list[LLM1Requirement] = Field(min_length=1)
    format_spec: FormatSpec
    meta_prompt_for_llm2: StrictStr
    unknown_or_ambiguous: list[UnknownOrAmbiguous] = Field(default_factory=list)


class ProjectMetadata(StrictBaseModel):
    title: StrictStr
    author: StrictStr
    keywords: list[StrictStr] = Field(default_factory=list)
    grant_call: Optional[StrictStr] = None


class ExpectedEvidence(StrictBaseModel):
    source: Literal["KB1", "KB2", "TOOLS"]
    note: StrictStr


class TargetLength(StrictBaseModel):
    words: Optional[conint(ge=0)] = None
    pages: Optional[conint(ge=0)] = None


class OutlineSection(StrictBaseModel):
    id: SecIdStr
    title: StrictStr
    purpose: StrictStr
    what_to_write: list[StrictStr] = Field(min_length=1)
    compliance_mapping: list[ReqIdStr] = Field(default_factory=list)
    expected_evidence: list[ExpectedEvidence] = Field(default_factory=list)
    subsections: list["OutlineSection"] = Field(default_factory=list)
    target_length: TargetLength


class AnnexItem(StrictBaseModel):
    id: AnnexIdStr
    title: StrictStr
    required_by: list[ReqIdStr] = Field(default_factory=list)
    content_plan: StrictStr


class OpponentQuestion(StrictBaseModel):
    question: StrictStr
    why_needed: StrictStr
    where_it_affects_outline: list[SecIdStr] = Field(default_factory=list)


class ProposalOutlineOutput(StrictBaseModel):
    language: StrictStr
    project_metadata: ProjectMetadata
    outline: list[OutlineSection] = Field(min_length=1)
    annexes: list[AnnexItem] = Field(default_factory=list)
    open_questions_for_opponent: list[OpponentQuestion] = Field(default_factory=list)


class MissingRequirement(StrictBaseModel):
    req_id: ReqIdStr
    problem: StrictStr
    evidence: list[KB1Evidence] = Field(default_factory=list)
    fix_suggestion: StrictStr


class OtherComplianceIssue(StrictBaseModel):
    type: Literal["format", "annex", "ethics", "budget", "logic", "other"]
    problem: StrictStr
    fix: StrictStr


class ComplianceAssessment(StrictBaseModel):
    is_compliant: bool
    missing_requirements: list[MissingRequirement] = Field(default_factory=list)
    other_issues: list[OtherComplianceIssue] = Field(default_factory=list)


class ScientificGap(StrictBaseModel):
    where: StrictStr
    gap: StrictStr
    why_it_matters: StrictStr
    suggested_fix: StrictStr


class ScientificAssessment(StrictBaseModel):
    is_thematically_aligned: bool
    gaps: list[ScientificGap] = Field(default_factory=list)
    innovation_score_0_10: conint(ge=0, le=10)
    feasibility_score_0_10: conint(ge=0, le=10)


class UserQuestion(StrictBaseModel):
    id: QuestionIdStr
    question: StrictStr
    why_needed: StrictStr
    where_to_insert_in_proposal_input: StrictStr
    if_user_refuses_then_write: StrictStr


class ProposalReviewOutput(StrictBaseModel):
    language: StrictStr
    compliance_assessment: ComplianceAssessment
    scientific_assessment: ScientificAssessment
    user_questions: list[UserQuestion] = Field(default_factory=list)
    instructions_to_llm1: list[StrictStr] = Field(default_factory=list)
    instructions_to_llm2: list[StrictStr] = Field(default_factory=list)
    should_iterate: bool
    stop_reason: StrictStr


class SourceEntry(StrictBaseModel):
    source_id: SourceIdStr
    source_keys: list[StrictStr] = Field(default_factory=list)
    source_type: Literal["kb1", "kb2", "web", "tool", "user", "unknown"]
    title: StrictStr
    authors: list[StrictStr] = Field(default_factory=list)
    year: Optional[conint(ge=0, le=2100)] = None
    url: Optional[StrictStr] = None
    doi: Optional[StrictStr] = None
    arxiv_id: Optional[StrictStr] = None
    accessed_date: Optional[StrictStr] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    publisher: Optional[StrictStr] = None
    venue: Optional[StrictStr] = None
    kb_source_path: Optional[StrictStr] = None
    kb_loc: Optional[StrictStr] = None
    evidence_rids: list[RidStr] = Field(default_factory=list)
    notes: Optional[StrictStr] = None


class ProposalSources(StrictBaseModel):
    sources: list[SourceEntry] = Field(min_length=1)
