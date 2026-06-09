from __future__ import annotations

from autogenbook.prompts.registry import get_prompt
from autogenbook.schemas.analyze import AnalyzeAgentOutput

from .base import BaseAgent
from .registry import register_agent


@register_agent("analyze")
class AnalyzeAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            name="analyze",
            system_prompt_template=get_prompt("analyze_agent_system"),
            user_prompt_template=get_prompt("analyze_agent_user"),
            output_type="object",
            output_model=AnalyzeAgentOutput,
        )
