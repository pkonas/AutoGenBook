from __future__ import annotations

from typing import Any

from .logging import get_logger
from .pipelines.book_pipeline import run_book
from .pipelines.paper_pipeline import run_paper
from .pipelines.presentation_pipeline import run_presentation
from .pipelines.proposal_pipeline import run_proposal
from .pipelines.scientist_pipeline import run_scientist
from .pipelines.reviewer_pipeline import run_reviewer
from mcp_gateway import report_mcp_status


def run(args: Any) -> int:
    run_ctx = getattr(args, "run_ctx", None)
    out_dir = getattr(run_ctx, "out_dir", None) if run_ctx is not None else None
    logger = get_logger("autogenbook", out_dir=out_dir)
    report_mcp_status(logger=logger)

    mode = getattr(args, "mode", "book")
    if mode == "book":
        return run_book(args, run_ctx, logger)
    if mode == "paper":
        return run_paper(args, run_ctx, logger)
    if mode == "presentation":
        return run_presentation(args, run_ctx, logger)
    if mode == "scientist":
        return run_scientist(args, run_ctx, logger)
    if mode == "proposal":
        return run_proposal(args, run_ctx, logger)
    if mode == "reviewer":
        return run_reviewer(args, run_ctx, logger)
    logger.error("Unknown mode '%s'.", mode)
    return 1
