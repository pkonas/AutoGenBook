from __future__ import annotations

from .base import AgentContext, BaseAgent
from .literature_agent import LiteratureAgent, WebLiteratureAgent
from .idea import IdeaAgent
from .plan import PlanAgent
from .review import ReviewAgent
from .code_patch_agent import CodePatchAgent
from .analyze_agent import AnalyzeAgent
from .revision_agent import RevisionAgent
from .context_memory_agent import ContextMemoryAgent
from .book_section_writer import BookSectionWriterAgent
from .book_section_reviewer import BookSectionReviewerAgent
from .book_section_revision import BookSectionRevisionAgent
from .structure_subdivider import StructureSubdividerAgent
from .section_reviewer import SectionReviewerAgent
from .section_revision import SectionRevisionAgent
from .paper_writer import PaperSectionWriterAgent
from .presentation_slide_writer import PresentationSlideWriterAgent
from .registry import get_agent, register_agent

__all__ = [
    "AgentContext",
    "BaseAgent",
    "IdeaAgent",
    "PlanAgent",
    "ReviewAgent",
    "CodePatchAgent",
    "AnalyzeAgent",
    "RevisionAgent",
    "ContextMemoryAgent",
    "BookSectionWriterAgent",
    "BookSectionReviewerAgent",
    "BookSectionRevisionAgent",
    "StructureSubdividerAgent",
    "LiteratureAgent",
    "WebLiteratureAgent",
    "SectionReviewerAgent",
    "SectionRevisionAgent",
    "PaperSectionWriterAgent",
    "PresentationSlideWriterAgent",
    "get_agent",
    "register_agent",
]
