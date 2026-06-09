from __future__ import annotations

from typing import Any, Dict, Optional

from autogenbook.prompts.registry import get_prompt
from autogenbook.schemas.book_review import BookSectionReviewerOutput
from openrouter_llm import OpenRouterLLM

from .base import AgentContext, BaseAgent
from .registry import register_agent


@register_agent("section_reviewer")
class SectionReviewerAgent(BaseAgent):
    def __init__(self, llm: Optional[OpenRouterLLM] = None) -> None:
        super().__init__(
            name="section_reviewer",
            system_prompt_template=get_prompt("book_section_reviewer_system"),
            user_prompt_template=get_prompt("book_section_reviewer_user"),
            output_type="object",
            llm=llm,
            output_model=BookSectionReviewerOutput,
        )

    def run(self, input: Dict[str, Any], context: AgentContext) -> Dict[str, Any]:
        prompt_input = {
            "section_title": input.get("section_title", ""),
            "node_key": input.get("node_key", ""),
            "section_latex": input.get("section_tex", ""),
            "context_memory_excerpt": input.get("context_memory", "(none)"),
            "previous_sections": input.get("previous_sections", "(none)"),
            "toc_and_summary": input.get("toc_and_summary", "(outline not provided)"),
            "retrieved_context": input.get("retrieved_context", "(none)"),
        }
        output = super().run(prompt_input, context)
        return output
