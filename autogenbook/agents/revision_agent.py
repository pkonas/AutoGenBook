from __future__ import annotations

from typing import Any, Dict

from autogenbook.prompts.registry import get_prompt
from openrouter_llm import OpenRouterLLM

from .base import AgentContext, BaseAgent
from .io_log import write_agent_io
from .registry import register_agent


@register_agent("revision")
class RevisionAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            name="revision",
            system_prompt_template=get_prompt("revision_agent_system"),
            user_prompt_template=get_prompt("revision_agent_user"),
            output_type="text",
        )

    def run(self, input: Dict[str, Any], context: AgentContext) -> str:
        output = super().run(input, context)
        from utils import extract_tex_fence
        from book_builder import _sanitize_section_tex

        tex = extract_tex_fence(output)
        tex, _ = _sanitize_section_tex(tex)
        return tex
