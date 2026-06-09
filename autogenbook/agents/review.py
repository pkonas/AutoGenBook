from __future__ import annotations

from autogenbook.prompts.registry import get_prompt
from autogenbook.schemas.review import ReviewAgentOutput

from .base import BaseAgent
from .registry import register_agent


@register_agent("review")
class ReviewAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            name="review",
            system_prompt_template=get_prompt("review_agent_system"),
            user_prompt_template=get_prompt("review_agent_user"),
            output_type="object",
            output_model=ReviewAgentOutput,
        )
