from __future__ import annotations

from typing import Any, Dict

from autogenbook.prompts.agent_prompts import render
from autogenbook.prompts.registry import get_prompt
from openrouter_llm import OpenRouterLLM

from .base import AgentContext
from .io_log import write_agent_io


class BookSectionWriterAgent:
    def __init__(self, llm: OpenRouterLLM) -> None:
        self.name = "book_section_writer"
        self.llm = llm

    def run(self, input: Dict[str, Any], context: AgentContext) -> str:
        llm = context.llm or self.llm
        if llm is None:
            raise RuntimeError("BookSectionWriterAgent requires an LLM in AgentContext or constructor.")

        prompt_args = dict(input)
        prompt_args.setdefault("retrieved_context", "(none)")
        prompt_args.setdefault("section_draft", "")
        system_prompt = render(get_prompt("book_section_writer_system"), **prompt_args)
        user_prompt = render(get_prompt("book_section_writer_user"), **prompt_args)

        raw = llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            allow_tools=False,
        )

        from utils import extract_markdown_fence, extract_tex_fence, normalize_markdown_paragraphs
        from book_builder import _sanitize_section_tex

        user_prompt_lc = user_prompt.lower()
        wants_markdown = "markdown body" in user_prompt_lc or "return only the markdown body" in user_prompt_lc
        if wants_markdown:
            tex = extract_markdown_fence(raw)
            tex = normalize_markdown_paragraphs(tex).strip()
        else:
            tex = extract_tex_fence(raw)
            tex, _ = _sanitize_section_tex(tex)

        usage = llm.get_last_usage()
        context.record_usage(usage, llm=llm, label=self.name)
        write_agent_io(
            context.out_dir,
            self.name,
            input,
            tex,
            {
                "run_id": context.run_id,
                "usage": usage,
                "cost_usd": getattr(llm, "get_last_cost_usd", lambda: None)(),
                "total_cost_usd": getattr(llm, "get_total_cost_usd", lambda: None)(),
                "model": llm.config.model,
                "temperature": llm.config.temperature,
            },
        )
        return tex
