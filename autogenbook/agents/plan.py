from __future__ import annotations

from autogenbook.prompts.registry import get_prompt
from autogenbook.schemas.plan import PlanAgentOutput

from .base import BaseAgent
from .registry import register_agent


@register_agent("plan")
class PlanAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            name="plan",
            system_prompt_template=get_prompt("plan_agent_system"),
            user_prompt_template=get_prompt("plan_agent_user"),
            output_type="object",
            output_model=PlanAgentOutput,
        )
