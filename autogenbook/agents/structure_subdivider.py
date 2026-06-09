from __future__ import annotations

from typing import Any, Dict, List

from autogenbook.prompts.registry import get_prompt
from autogenbook.schemas.subdivide import SubdivideOutput

from .base import AgentContext, BaseAgent
from .registry import register_agent


@register_agent("structure_subdivider")
class StructureSubdividerAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            name="structure_subdivider",
            system_prompt_template=get_prompt("structure_subdivider_system"),
            user_prompt_template=get_prompt("structure_subdivider_user"),
            output_type="array",
            output_model=SubdivideOutput,
        )

    def run(self, input: Dict[str, Any], context: AgentContext) -> List[Dict[str, Any]]:
        output = super().run(input, context)
        if not isinstance(output, list):
            return []
        return output
