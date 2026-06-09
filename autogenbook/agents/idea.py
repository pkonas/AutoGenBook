from __future__ import annotations

from autogenbook.prompts.registry import get_prompt
from autogenbook.schemas.idea import IdeaAgentOutput

from .base import BaseAgent
from .registry import register_agent


@register_agent("idea")
class IdeaAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            name="idea",
            system_prompt_template=get_prompt("idea_agent_system"),
            user_prompt_template=get_prompt("idea_agent_user"),
            output_type="object",
            output_model=IdeaAgentOutput,
        )
