from __future__ import annotations

from typing import Any, Dict, Optional

from autogenbook.prompts.registry import get_prompt
from autogenbook.schemas.memory import ContextMemoryUpdateOutput
from openrouter_llm import OpenRouterLLM

from .base import AgentContext, BaseAgent
from .registry import register_agent


@register_agent("context_memory")
class ContextMemoryAgent(BaseAgent):
    def __init__(self, llm: Optional[OpenRouterLLM] = None) -> None:
        super().__init__(
            name="context_memory",
            system_prompt_template=get_prompt("context_memory_system"),
            user_prompt_template=get_prompt("context_memory_user"),
            output_type="object",
            llm=llm,
            output_model=ContextMemoryUpdateOutput,
        )

    def run(self, input: Dict[str, Any], context: AgentContext) -> Dict[str, Any]:
        output = super().run(input, context)
        return output
