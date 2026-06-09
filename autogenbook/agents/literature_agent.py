from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from autogenbook.prompts.agent_prompts import render
from autogenbook.prompts.registry import get_prompt
from autogenbook.schemas.literature import LiteratureAgentOutput
from openrouter_llm import OpenRouterLLM

from .base import AgentContext, BaseAgent
from .io_log import write_agent_io
from .registry import register_agent


def _slugify(text: str, max_len: int = 40) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return cleaned[:max_len] or "ref"


def _bibtex_key(item_id: str, title: str) -> str:
    if item_id.startswith("web:doi:"):
        return "doi" + _slugify(item_id.replace("web:doi:", ""))
    return _slugify(title)


def _build_bibtex(entry: Dict[str, Any]) -> str:
    title = entry.get("title") or "Untitled"
    authors = entry.get("authors") or []
    year = entry.get("year")
    venue = entry.get("venue") or ""
    doi = entry.get("doi")
    url = entry.get("url") or ""
    key = entry.get("bib_key") or _bibtex_key(entry.get("source_id", ""), title)
    author_field = " and ".join(authors) if authors else "Unknown"
    if venue:
        return (
            "@article{"
            + key
            + ",\n"
            + f"  title={{{title}}},\n"
            + f"  author={{{author_field}}},\n"
            + (f"  journal={{{venue}}},\n" if venue else "")
            + (f"  year={{{year}}},\n" if year else "")
            + (f"  doi={{{doi}}},\n" if doi else "")
            + (f"  url={{{url}}},\n" if url else "")
            + "}\n"
        )
    return (
        "@misc{"
        + key
        + ",\n"
        + f"  title={{{title}}},\n"
        + f"  author={{{author_field}}},\n"
        + (f"  year={{{year}}},\n" if year else "")
        + (f"  doi={{{doi}}},\n" if doi else "")
        + (f"  url={{{url}}},\n" if url else "")
        + "}\n"
    )


@register_agent("literature")
class LiteratureAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            name="literature",
            system_prompt_template=get_prompt("literature_agent_system"),
            user_prompt_template=get_prompt("literature_agent_user"),
            output_type="object",
            output_model=LiteratureAgentOutput,
        )


class WebLiteratureAgent:
    def __init__(self, llm: OpenRouterLLM) -> None:
        self.name = "literature_web"
        self.llm = llm
        self.system_prompt_template = get_prompt("web_literature_system")
        self.user_prompt_template = get_prompt("web_literature_user")

    def run(self, input: Dict[str, Any], context: AgentContext) -> Dict[str, Any]:
        if context.retrieval_manager is None:
            return {
                "related_papers": [],
                "novelty_risks": ["No retrieval manager available; cannot check novelty."],
                "positioning_statement": "Online related work not available.",
            }

        idea = str(input.get("idea", "")).strip()
        keywords = input.get("keywords", [])
        if isinstance(keywords, list):
            keywords_text = ", ".join([str(k) for k in keywords if k])
        else:
            keywords_text = str(keywords)

        query = " ".join([idea, keywords_text]).strip()
        items = context.retrieval_manager.retrieve(query, k=8, allow_web=False)
        web_items = [i for i in items if i.kind == "web"]

        if not web_items:
            return {
                "related_papers": [],
                "novelty_risks": ["No online related work retrieved."],
                "positioning_statement": "Unable to confirm novelty due to missing related work.",
            }

        candidates: List[Dict[str, Any]] = []
        for item in web_items:
            entry = {
                "source_id": item.rid,
                "title": item.title,
                "authors": item.authors,
                "year": item.year,
                "venue": item.venue,
                "url": item.url,
                "doi": item.doi,
            }
            entry["bib_key"] = item.cite_key or _bibtex_key(item.rid, item.title or "")
            entry["bibtex"] = _build_bibtex(entry)
            entry["abstract"] = item.text
            candidates.append(entry)

        prompt = json.dumps(
            {
                "idea": idea,
                "keywords": keywords,
                "candidates": candidates,
            },
            ensure_ascii=False,
            indent=2,
        )

        llm = context.llm or self.llm
        if llm is None:
            raise RuntimeError("WebLiteratureAgent requires an LLM in AgentContext or constructor.")

        system_prompt = render(self.system_prompt_template)
        user_prompt = render(self.user_prompt_template, prompt=prompt)
        output = llm.chat_json(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            expect="object",
        )

        related = []
        by_id = {c["source_id"]: c for c in candidates}
        for paper in output.get("related_papers", []) if isinstance(output, dict) else []:
            source_id = paper.get("source_id")
            if source_id not in by_id:
                continue
            candidate = by_id[source_id]
            paper["title"] = candidate["title"]
            paper["authors"] = candidate["authors"]
            paper["year"] = candidate["year"]
            paper["venue"] = candidate["venue"]
            paper["url"] = candidate["url"]
            paper["bibtex"] = candidate["bibtex"]
            paper["bib_key"] = candidate["bib_key"]
            related.append(paper)

        cleaned = {
            "related_papers": related,
            "novelty_risks": output.get("novelty_risks", []) if isinstance(output, dict) else [],
            "positioning_statement": output.get("positioning_statement", ""),
        }

        usage = llm.get_last_usage()
        context.record_usage(usage, llm=llm, label=self.name)
        write_agent_io(
            context.out_dir,
            self.name,
            input,
            cleaned,
            {
                "run_id": context.run_id,
                "usage": usage,
                "cost_usd": getattr(llm, "get_last_cost_usd", lambda: None)(),
                "total_cost_usd": getattr(llm, "get_total_cost_usd", lambda: None)(),
                "model": llm.config.model,
                "temperature": llm.config.temperature,
            },
        )
        return cleaned
