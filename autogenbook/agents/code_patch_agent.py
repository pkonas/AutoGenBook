from __future__ import annotations

from autogenbook.prompts.registry import get_prompt
from autogenbook.schemas.code_patch import CodePatchAgentOutput

from .base import BaseAgent
from .registry import register_agent


@register_agent("code_patch")
class CodePatchAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            name="code_patch",
            system_prompt_template=get_prompt("code_patch_agent_system"),
            user_prompt_template=get_prompt("code_patch_agent_user"),
            output_type="object",
            output_model=CodePatchAgentOutput,
        )
