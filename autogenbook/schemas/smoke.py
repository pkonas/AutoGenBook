from __future__ import annotations

from autogenbook.schemas import (
    AnalyzeAgentOutput,
    BookSectionReviewerOutput,
    CodePatchAgentOutput,
    ContextMemoryUpdateOutput,
    EvidenceRef,
    IdeaAgentOutput,
    LiteratureAgentOutput,
    PlanAgentOutput,
    ReviewAgentOutput,
    SubdivideOutput,
    format_validation_error,
    validate_or_raise,
    common,
)
from autogenbook.schemas.samples import SAMPLES
from pydantic import ValidationError


def main() -> None:
    _ = common
    _ = EvidenceRef(**SAMPLES["evidence_ref"])
    _ = IdeaAgentOutput(**SAMPLES["idea"])
    _ = LiteratureAgentOutput(**SAMPLES["literature"])
    _ = PlanAgentOutput(**SAMPLES["plan"])
    _ = CodePatchAgentOutput(**SAMPLES["code_patch"])
    _ = AnalyzeAgentOutput(**SAMPLES["analyze"])
    _ = ReviewAgentOutput(**SAMPLES["review"])
    _ = BookSectionReviewerOutput(**SAMPLES["book_section_review"])
    _ = ContextMemoryUpdateOutput(**SAMPLES["context_memory_update"])
    _ = SubdivideOutput(**SAMPLES["subdivide"])
    try:
        validate_or_raise(SubdivideOutput, {"items": []})
    except ValidationError as exc:
        print("validation error sample:")
        print(format_validation_error(exc))
    # Negative test example (uncomment to see validation error):
    # _ = ReviewAgentOutput(
    #     overall_summary="Summary",
    #     scores={
    #         "novelty": 0,
    #         "technical_quality": 5,
    #         "clarity": 5,
    #         "evidence_grounding": 5,
    #         "reproducibility": 5,
    #     },
    #     strengths=["Strength"],
    #     weaknesses=["Weakness"],
    #     major_issues=[],
    #     missing_experiments=[],
    #     citation_problems=[],
    #     suggested_revisions=[],
    #     verdict="borderline",
    # )
    print("ok")


if __name__ == "__main__":
    main()
