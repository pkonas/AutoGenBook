from __future__ import annotations

from typing import Any, Dict

from autogenbook.prompts.agent_prompts import render
from autogenbook.prompts.registry import get_prompt
from openrouter_llm import OpenRouterLLM

from .base import AgentContext
from .io_log import write_agent_io
from .registry import register_agent


@register_agent("section_revision")
class SectionRevisionAgent:
    def __init__(self, llm: OpenRouterLLM) -> None:
        self.name = "section_revision"
        self.llm = llm

    def run(self, input: Dict[str, Any], context: AgentContext) -> str:
        llm = context.llm or self.llm
        if llm is None:
            raise RuntimeError("SectionRevisionAgent requires an LLM in AgentContext or constructor.")

        prompt_args = {
            "section_latex": input.get("section_tex", ""),
            "review_json": input.get("review", ""),
            "context_memory_excerpt": input.get("context_memory", "(none)"),
            "retrieved_context": input.get("retrieved_context", "(none)"),
        }
        system_prompt = render(get_prompt("book_section_revision_system"), **prompt_args)
        user_prompt = render(get_prompt("book_section_revision_user"), **prompt_args)

        output = llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            allow_tools=False,
        )
        usage = llm.get_last_usage()
        context.record_usage(usage, llm=llm, label=self.name)
        write_agent_io(
            context.out_dir,
            self.name,
            input,
            output,
            {
                "run_id": context.run_id,
                "usage": usage,
                "cost_usd": getattr(llm, "get_last_cost_usd", lambda: None)(),
                "total_cost_usd": getattr(llm, "get_total_cost_usd", lambda: None)(),
                "model": llm.config.model,
                "temperature": llm.config.temperature,
            },
        )
        return output
