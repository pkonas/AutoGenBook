from __future__ import annotations

from typing import Any, Dict

from autogenbook.prompts.agent_prompts import render
from autogenbook.prompts.registry import get_prompt
from openrouter_llm import OpenRouterLLM

from .base import AgentContext
from .io_log import write_agent_io


class PresentationSlideWriterAgent:
    def __init__(self, llm: OpenRouterLLM) -> None:
        self.name = "presentation_slide_writer"
        self.llm = llm

    def run(self, input: Dict[str, Any], context: AgentContext) -> str:
        llm = context.llm or self.llm
        if llm is None:
            raise RuntimeError("PresentationSlideWriterAgent requires an LLM in AgentContext or constructor.")

        prompt_args = dict(input)
        prompt_args.setdefault("retrieved_context", "(none)")
        prompt_args.setdefault("slide_draft", "")
        system_prompt = render(get_prompt("presentation_slide_writer_system"), **prompt_args)
        user_prompt = render(get_prompt("presentation_slide_writer_user"), **prompt_args)

        raw = llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            allow_tools=False,
        )

        from utils import extract_markdown_fence

        md = extract_markdown_fence(raw).strip()

        usage = llm.get_last_usage()
        context.record_usage(usage, llm=llm, label=self.name)
        write_agent_io(
            context.out_dir,
            self.name,
            input,
            md,
            {
                "run_id": context.run_id,
                "usage": usage,
                "cost_usd": getattr(llm, "get_last_cost_usd", lambda: None)(),
                "total_cost_usd": getattr(llm, "get_total_cost_usd", lambda: None)(),
                "model": llm.config.model,
                "temperature": llm.config.temperature,
            },
        )
        return md
