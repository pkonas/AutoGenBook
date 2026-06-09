from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx

from autogenbook.agents.base import AgentContext
from autogenbook.agents import StructureSubdividerAgent
from autogenbook.agents.presentation_slide_writer import PresentationSlideWriterAgent
from autogenbook.graph.doc_graph import (
    attach_content_path,
    leaf_nodes_in_order,
    load_graph_json,
    save_graph_json,
    sort_node_keys,
)
from autogenbook.length_control import enforce_section_length
from autogenbook.llm_usage import get_openrouter_usage_totals, log_usage, write_run_meta
from autogenbook.presentation_export import (
    md_to_pptx,
    md_to_beamer_tex,
    parse_slide_ranges,
    split_presentation_markdown,
)
from autogenbook.presentation_media import (
    DEFAULT_IMAGE_MODEL,
    DEFAULT_OPENROUTER_TTS_MODEL,
    format_srt_timestamp,
    generate_slide_image_openrouter,
    render_video_from_pdf,
    synthesize_speech_local_tts,
    synthesize_speech_openrouter_tts,
)
from autogenbook.prompts.agent_prompts import render
from autogenbook.prompts.presentation_loader import load_presentation_prompts
from autogenbook.prompts.registry import get_prompt, set_prompt_registry
from autogenbook.retrieval.manager import RetrievalManager
from autogenbook.retrieval.mcp_papers import MCPPaperRetriever
from autogenbook.retrieval.tavily import TavilyRetriever
from openrouter_llm import OpenRouterLLM
from rag_kb import KnowledgeBase
from utils import (
    build_retrieval_query_from_tex,
    ensure_bool,
    ensure_float,
    ensure_int,
    extract_first_json_object,
    generate_outline_text,
    safe_filename,
)

from ..state import RunContext


LINES_PER_SLIDE = 10
MAX_LINES_PER_SLIDE = 10


def _normalize_slide_line(line: str) -> str:
    cleaned = line.strip()
    cleaned = re.sub(r"^[-*+]\s+", "", cleaned)
    cleaned = re.sub(r"^\d+\.\s+", "", cleaned)
    cleaned = re.sub(r"`{1,3}(.+?)`{1,3}", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.lower()


_IMAGE_EMBED_RE = re.compile(r"!\[\[(.*?)\]\]")
_KB_MARKER_PATTERNS = [
    re.compile(r"\(\s*(?:viz|see)\s+kb\s*\)", re.IGNORECASE),
    re.compile(r"\(\s*kb\s*\)", re.IGNORECASE),
    re.compile(r"\[\s*kb\s*\]", re.IGNORECASE),
    re.compile(r"\b(?:viz|see)\s+kb\b", re.IGNORECASE),
    re.compile(r"\bsource\s*:\s*kb\b", re.IGNORECASE),
    re.compile(r"\bsource\s+kb\b", re.IGNORECASE),
    re.compile(r"\bkb\b\s*$", re.IGNORECASE),
]
_GENERAL_KNOWLEDGE_PATTERNS = [
    re.compile(r"\*+\s*general background knowledge\s*:?\s*\*+", re.IGNORECASE),
    re.compile(r"\bgeneral background knowledge\b\s*:?", re.IGNORECASE),
]


def _strip_image_embeds(text: str) -> str:
    return _IMAGE_EMBED_RE.sub("", text).strip()


def _strip_kb_markers(text: str) -> str:
    cleaned_lines: List[str] = []
    for line in text.splitlines():
        cleaned = line
        for pattern in _KB_MARKER_PATTERNS:
            cleaned = pattern.sub("", cleaned)
        cleaned = re.sub(r"\(\s*\)", "", cleaned)
        cleaned = re.sub(r"\[\s*\]", "", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
        if re.match(r"^[-*+]\s*$", cleaned):
            continue
        if cleaned:
            cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines).strip()


def _strip_general_knowledge_markers(text: str) -> str:
    cleaned_lines: List[str] = []
    for line in text.splitlines():
        cleaned = line
        for pattern in _GENERAL_KNOWLEDGE_PATTERNS:
            cleaned = pattern.sub("", cleaned)
        cleaned = re.sub(r"\(\s*\)", "", cleaned)
        cleaned = re.sub(r"\[\s*\]", "", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
        if re.match(r"^[-*+]\s*$", cleaned):
            continue
        if cleaned:
            cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines).strip()


def _update_seen_lines(seen: set[str], slide_md: str) -> None:
    for line in slide_md.splitlines():
        if not line.strip():
            continue
        key = _normalize_slide_line(line)
        if key:
            seen.add(key)


def _dedupe_slide_lines(slide_md: str, seen: set[str]) -> str:
    out_lines: List[str] = []
    for line in slide_md.splitlines():
        if not line.strip():
            out_lines.append(line)
            continue
        key = _normalize_slide_line(line)
        if key and key in seen:
            continue
        out_lines.append(line)
        if key:
            seen.add(key)
    return "\n".join(out_lines).strip()


def _ensure_max_lines(slide_md: str, max_lines: int = MAX_LINES_PER_SLIDE) -> str:
    lines = slide_md.splitlines()
    if len(lines) <= max_lines:
        return slide_md.strip()
    non_blank = [line for line in lines if line.strip()]
    if len(non_blank) <= max_lines:
        return "\n".join(non_blank).strip()
    return "\n".join(non_blank[:max_lines]).strip()


def _summarize_previous_slides(prev_slides: List[Dict[str, str]], max_chars: int = 800) -> str:
    if not prev_slides:
        return "(none)"
    lines: List[str] = []
    for item in prev_slides:
        title = item.get("title", "").strip() or "Untitled"
        content = _strip_image_embeds(item.get("content", ""))
        content_lines = [line.strip() for line in content.splitlines() if line.strip()]
        snippet = "; ".join(content_lines[:2]) if content_lines else ""
        if snippet:
            lines.append(f"- {title}: {snippet}")
        else:
            lines.append(f"- {title}")
    summary = "\n".join(lines).strip()
    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip() + "..."
    return summary or "(none)"


def _build_image_prompt(
    *,
    presentation_title: str,
    presentation_style: str,
    slide_title: str,
    slide_body: str,
    previous_summary: str,
) -> str:
    system_prompt = get_prompt("presentation_image_prompt_system")
    user_prompt = render(
        get_prompt("presentation_image_prompt_user"),
        presentation_title=presentation_title,
        presentation_style=presentation_style,
        slide_title=slide_title,
        slide_body=slide_body,
        previous_summary=previous_summary,
    )
    return f"{system_prompt}\n\n{user_prompt}".strip()


def _format_scientific_sources(items: List[Any]) -> str:
    if not items:
        return "(none)"
    lines: List[str] = []
    for idx, item in enumerate(items, start=1):
        author_list = [a.strip() for a in (getattr(item, "authors", None) or []) if a and str(a).strip()]
        authors = ", ".join(author_list)
        year = str(item.year) if getattr(item, "year", None) else "n.d."
        title = str(item.title or "").strip()
        venue = str(item.venue or "").strip()
        url = str(item.url or "").strip()
        harvard_author = "Unknown"
        if author_list:
            if len(author_list) == 1:
                harvard_author = author_list[0].split()[-1]
            elif len(author_list) == 2:
                harvard_author = f"{author_list[0].split()[-1]} and {author_list[1].split()[-1]}"
            else:
                harvard_author = f"{author_list[0].split()[-1]} et al."
        harvard = f"{harvard_author} {year}"
        lines.append(
            f"[SRC{idx}] {title} | Authors: {authors or 'Unknown'} | Year: {year} | "
            f"Venue: {venue or 'n/a'} | Harvard: ({harvard}) | URL: {url or 'n/a'}"
        )
    return "\n".join(lines)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    mins, sec = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    if hrs:
        return f"{hrs}h {mins}m {sec}s"
    if mins:
        return f"{mins}m {sec}s"
    return f"{sec}s"


def _format_int(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{int(value):,}"
    except Exception:
        return "n/a"


def _format_cost(value: Optional[float], *, estimated: bool = False) -> str:
    if value is None:
        return "n/a"
    prefix = "~" if estimated else ""
    return f"{prefix}${float(value):.6f}"


def _extract_overall_usage_totals() -> Dict[str, Optional[float]]:
    totals = get_openrouter_usage_totals()
    overall = totals.get("overall") or {}
    return {
        "input_tokens": overall.get("input_tokens"),
        "output_tokens": overall.get("output_tokens"),
        "total_tokens": overall.get("total_tokens"),
        "input_cost_usd": overall.get("input_cost_usd"),
        "output_cost_usd": overall.get("output_cost_usd"),
        "total_cost_usd": overall.get("total_cost_usd"),
    }


def _diff_totals(
    current: Dict[str, Optional[float]],
    base: Dict[str, Optional[float]],
) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "input_cost_usd",
        "output_cost_usd",
        "total_cost_usd",
    ):
        cur_val = current.get(key)
        base_val = base.get(key)
        if cur_val is None:
            out[key] = None
        elif base_val is None:
            out[key] = cur_val
        else:
            out[key] = cur_val - base_val
    return out


def _estimate_cost_from_tokens(
    input_tokens: Optional[float],
    output_tokens: Optional[float],
    llm: Optional[OpenRouterLLM],
) -> Dict[str, Optional[float]]:
    if llm is None:
        return {"input_cost_usd": None, "output_cost_usd": None, "total_cost_usd": None}
    rate_in = getattr(llm, "input_cost_per_million", None)
    rate_out = getattr(llm, "output_cost_per_million", None)
    if rate_in is None or rate_out is None:
        return {"input_cost_usd": None, "output_cost_usd": None, "total_cost_usd": None}
    in_cost = None
    out_cost = None
    if input_tokens is not None:
        in_cost = (float(input_tokens) * float(rate_in)) / 1_000_000.0
    if output_tokens is not None:
        out_cost = (float(output_tokens) * float(rate_out)) / 1_000_000.0
    total = None
    if in_cost is not None or out_cost is not None:
        total = (in_cost or 0.0) + (out_cost or 0.0)
    return {"input_cost_usd": in_cost, "output_cost_usd": out_cost, "total_cost_usd": total}


def _ask_choice(prompt: str, choices: Dict[str, str], default: str) -> str:
    keys = "/".join([k.upper() for k in choices.keys()])
    if os.environ.get("AUTOGENBOOK_NONINTERACTIVE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return default.lower()
    while True:
        ans = input(f"{prompt} [{keys}] (default {default.upper()}): ").strip().lower()
        if not ans:
            ans = default.lower()
        if ans in choices:
            return ans


def _ask_text(prompt: str, default: Optional[str] = None, required: bool = False) -> str:
    suffix = f" (default {default})" if default else ""
    if os.environ.get("AUTOGENBOOK_NONINTERACTIVE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return default or ""
    while True:
        ans = input(f"{prompt}{suffix}: ").strip()
        if ans:
            return ans
        if default:
            return default
        if not required:
            return ""
        print("Please enter a value.")


def _normalize_presentation_json(raw: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(raw)
    out["title"] = str(out.get("title", "")).strip() or "Untitled Presentation"
    out["summary"] = str(out.get("summary", "")).strip()
    out["audience"] = str(out.get("audience", "")).strip()
    out["duration_minutes"] = ensure_float(out.get("duration_minutes", 15), 15)
    out["style_guidance"] = str(out.get("style_guidance", "")).strip()
    out["theme"] = str(out.get("theme", "")).strip() or "Madrid"
    out["paginate"] = ensure_bool(out.get("paginate", False), False)
    out["outline"] = ensure_bool(out.get("outline", True), True)
    out["author"] = str(out.get("author", "")).strip()
    out["header"] = str(out.get("header", "")).strip()
    out["footer"] = str(out.get("footer", "")).strip()
    out["max_depth"] = ensure_int(out.get("max_depth", 3), 3)
    out["max_output_pages"] = ensure_float(out.get("max_output_pages", 1.0), 1.0)

    slides = out.get("slides") or out.get("childs") or out.get("sections") or []
    if not isinstance(slides, list):
        slides = []
    norm_slides = []
    for slide in slides:
        if not isinstance(slide, dict):
            continue
        n_pages = ensure_float(slide.get("n_pages", 1.0), 1.0)
        n_pages = max(0.1, n_pages)
        norm_slides.append(
            {
                "title": str(slide.get("title", "")).strip(),
                "summary": str(slide.get("summary", "")).strip(),
                "n_pages": n_pages,
                "needsSubdivision": ensure_bool(
                    slide.get("needsSubdivision", n_pages > out["max_output_pages"]),
                    n_pages > out["max_output_pages"],
                ),
            }
        )
    out["slides"] = norm_slides
    total_pages = sum(slide["n_pages"] for slide in norm_slides) if norm_slides else 0
    out["n_pages"] = ensure_float(out.get("n_pages", total_pages), total_pages)
    return out


def _build_presentation_json(
    llm: OpenRouterLLM,
    txt_spec: str,
    kb: Optional[KnowledgeBase],
) -> Dict[str, Any]:
    kb_context = ""
    if kb is not None:
        kb_context = kb.format_context(txt_spec, k=6, max_chars_total=6000)
    prompt_template = get_prompt("presentation_json_from_txt_user")
    prompt = render(prompt_template, txt_spec=txt_spec, kb_context=kb_context)
    content = llm.chat(
        [
            {"role": "system", "content": get_prompt("presentation_json_from_txt_system")},
            {"role": "user", "content": prompt},
        ],
        allow_tools=False,
    )
    usage = llm.get_last_usage()
    if usage:
        print(llm.format_usage_line(usage, label="presentation_json"))
    raw = extract_first_json_object(content)
    return _normalize_presentation_json(raw)


def _build_graph_from_presentation_json(presentation_json: Dict[str, Any]) -> nx.DiGraph:
    g = nx.DiGraph()
    g.graph.update(
        {
            "title": presentation_json.get("title", ""),
            "summary": presentation_json.get("summary", ""),
            "audience": presentation_json.get("audience", ""),
            "duration_minutes": presentation_json.get("duration_minutes", 15),
            "style_guidance": presentation_json.get("style_guidance", ""),
            "theme": presentation_json.get("theme", "Madrid"),
            "paginate": presentation_json.get("paginate", False),
            "outline": presentation_json.get("outline", True),
            "author": presentation_json.get("author", ""),
            "header": presentation_json.get("header", ""),
            "footer": presentation_json.get("footer", ""),
            "max_depth": int(presentation_json.get("max_depth", 3)),
            "max_output_pages": float(presentation_json.get("max_output_pages", 1.0)),
        }
    )
    g.add_node(
        "presentation",
        title=presentation_json.get("title", ""),
        summary=presentation_json.get("summary", ""),
        n_pages=presentation_json.get("n_pages", 0),
        needsSubdivision=True,
    )
    for i, slide in enumerate(presentation_json.get("slides", []), start=1):
        node = str(i)
        g.add_node(
            node,
            title=str(slide.get("title", "")).strip(),
            summary=str(slide.get("summary", "")).strip(),
            n_pages=float(slide.get("n_pages", 1.0)),
            needsSubdivision=bool(slide.get("needsSubdivision", True)),
        )
        g.add_edge("presentation", node)
    return g


def _node_children_sorted(g: nx.DiGraph, node: str) -> List[str]:
    return sort_node_keys(list(g.successors(node)))


def _subdivide_presentation_graph(
    llm: OpenRouterLLM,
    g: nx.DiGraph,
    kb: Optional[KnowledgeBase],
    retrieval_manager: Optional[RetrievalManager] = None,
    progress_path: Optional[Path] = None,
    fail_fast_schema: bool = False,
) -> None:
    max_depth = int(g.graph.get("max_depth", 3))
    max_output_pages = float(g.graph.get("max_output_pages", 1.0))
    title = g.graph.get("title", "")
    summary = g.graph.get("summary", "")
    audience = g.graph.get("audience", "")
    if retrieval_manager is None:
        retrieval_manager = RetrievalManager(local_kb=kb, default_k=6, max_chars_total=6000)
    subdivider = StructureSubdividerAgent()
    run_id = time.strftime("%Y%m%dT%H%M%SZ")
    out_dir = progress_path.parent if progress_path is not None else Path.cwd()
    agent_ctx = AgentContext(
        run_id=run_id,
        out_dir=out_dir,
        kb=kb,
        llm=llm,
        mode="presentation",
        retrieval_manager=retrieval_manager,
        fail_fast_schema=fail_fast_schema,
    )

    frontier = ["presentation"]
    for depth in range(1, max_depth + 1):
        next_frontier: List[str] = []
        for parent in frontier:
            for child in _node_children_sorted(g, parent):
                node = g.nodes[child]
                needs_sub = bool(node.get("needsSubdivision", False))
                n_pages = float(node.get("n_pages", 1.0))
                should_subdivide = (needs_sub or n_pages > max_output_pages) and depth < max_depth
                if not should_subdivide:
                    continue

                query = f"{title}\n{summary}\n{node.get('title','')}\n{node.get('summary','')}"
                retrieved_context = ""
                if retrieval_manager is not None:
                    items = retrieval_manager.retrieve(query, k=6, allow_web=False)
                    retrieved_context = retrieval_manager.format_context(items, max_chars_total=6000)
                if not retrieved_context:
                    retrieved_context = "(none)"

                max_attempts = 3
                section_list = None
                for attempt in range(1, max_attempts + 1):
                    try:
                        section_list = subdivider.run(
                            {
                                "doc_kind": "presentation",
                                "doc_title": title,
                                "doc_summary": summary,
                                "target_audience": audience,
                                "parent_title": node.get("title", ""),
                                "parent_summary": node.get("summary", ""),
                                "n_pages": n_pages,
                                "max_output_pages": max_output_pages,
                                "retrieved_context": retrieved_context,
                            },
                            agent_ctx,
                        )
                        if not isinstance(section_list, list) or not section_list:
                            raise ValueError("Empty or invalid JSON array.")
                        if not all(isinstance(item, dict) for item in section_list):
                            raise ValueError("JSON array items must be objects.")
                        break
                    except Exception as exc:
                        if attempt == max_attempts:
                            raise ValueError(
                                f"Failed to parse section list after {max_attempts} attempts."
                            ) from exc
                        continue

                for old in list(g.successors(child)):
                    g.remove_node(old)

                for i, sub in enumerate(section_list, start=1):
                    sub_key = f"{child}-{i}"
                    g.add_node(
                        sub_key,
                        title=str(sub.get("title", "")).strip(),
                        summary=str(sub.get("summary", "")).strip(),
                        n_pages=float(sub.get("n_pages", 1.0)),
                        needsSubdivision=bool(sub.get("needsSubdivision", False)),
                    )
                    g.add_edge(child, sub_key)

                next_frontier.append(child)
                if progress_path is not None:
                    save_graph_json(g, progress_path)

        if not next_frontier:
            break
        frontier = next_frontier


def _build_outline_text(g: nx.DiGraph) -> str:
    tree = {
        n: {
            "title": g.nodes[n].get("title", ""),
            "summary": g.nodes[n].get("summary", ""),
            "n_pages": g.nodes[n].get("n_pages", ""),
            "children": _node_children_sorted(g, n),
        }
        for n in g.nodes
    }
    return generate_outline_text(tree, root="presentation")


def _generate_slide_contents(
    llm: OpenRouterLLM,
    g: nx.DiGraph,
    out_dir: Path,
    kb: Optional[KnowledgeBase],
    retrieval_manager: RetrievalManager,
    progress_path: Optional[Path],
    resume: bool,
    citations_enabled: bool,
    mcp_papers: Optional[MCPPaperRetriever],
    image_model: str,
    generate_images: bool,
    disable_general_knowledge_citation: bool,
) -> None:
    slides_dir = out_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    images_dir: Optional[Path] = None
    if generate_images:
        images_dir = out_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

    title = g.graph.get("title", "")
    summary = g.graph.get("summary", "")
    audience = g.graph.get("audience", "")
    duration_minutes = g.graph.get("duration_minutes", 15)
    style_guidance = g.graph.get("style_guidance", "")
    outline_text = _build_outline_text(g)

    run_id = time.strftime("%Y%m%dT%H%M%SZ")
    agent_ctx = AgentContext(
        run_id=run_id,
        out_dir=out_dir,
        kb=kb,
        llm=llm,
        mode="presentation",
        retrieval_manager=retrieval_manager,
    )
    writer = PresentationSlideWriterAgent(llm)

    prev_slides: List[Dict[str, str]] = []
    seen_lines: set[str] = set()
    leaf_nodes = leaf_nodes_in_order(g)
    total_slides = len(leaf_nodes)
    progress_start = time.perf_counter()
    usage_start = _extract_overall_usage_totals()
    processed_slides = 0
    if total_slides:
        print(f"[Slides] Total: {total_slides}")

    def log_progress(*, completed: int) -> None:
        elapsed = time.perf_counter() - progress_start
        avg_time = elapsed / processed_slides if processed_slides else 0.0
        remaining = max(0, total_slides - completed)
        eta = avg_time * remaining if avg_time else 0.0

        current = _extract_overall_usage_totals()
        delta = _diff_totals(current, usage_start)

        estimated_costs = _estimate_cost_from_tokens(
            delta.get("input_tokens"),
            delta.get("output_tokens"),
            llm,
        )

        input_cost = delta.get("input_cost_usd")
        output_cost = delta.get("output_cost_usd")
        total_cost = delta.get("total_cost_usd")
        input_est = False
        output_est = False
        total_est = False
        if input_cost is None:
            input_cost = estimated_costs.get("input_cost_usd")
            input_est = input_cost is not None
        if output_cost is None:
            output_cost = estimated_costs.get("output_cost_usd")
            output_est = output_cost is not None
        if total_cost is None:
            total_cost = estimated_costs.get("total_cost_usd")
            total_est = total_cost is not None

        print(
            "[USAGE] IN={in_tok} OUT={out_tok} TOTAL={tot_tok} | "
            "Cost IN={in_cost} OUT={out_cost} TOTAL={total_cost}{cost_note}".format(
                in_tok=_format_int(delta.get("input_tokens")),
                out_tok=_format_int(delta.get("output_tokens")),
                tot_tok=_format_int(delta.get("total_tokens")),
                in_cost=_format_cost(input_cost, estimated=input_est),
                out_cost=_format_cost(output_cost, estimated=output_est),
                total_cost=_format_cost(total_cost, estimated=total_est),
                cost_note=" (est)" if input_est or output_est or total_est else "",
            )
        )

        if processed_slides:
            avg_cost = (total_cost or 0.0) / processed_slides if total_cost is not None else None
            est_total = avg_cost * total_slides if avg_cost is not None else None
            est_remaining = avg_cost * remaining if avg_cost is not None else None
            print(
                "[ETA] Elapsed {elapsed}, Avg/slide {avg}, ETA {eta} | "
                "Est total cost {est_total}, remaining {est_rem}".format(
                    elapsed=_format_duration(elapsed),
                    avg=_format_duration(avg_time),
                    eta=_format_duration(eta),
                    est_total=_format_cost(est_total, estimated=avg_cost is not None),
                    est_rem=_format_cost(est_remaining, estimated=avg_cost is not None),
                )
            )
    for idx, node_key in enumerate(leaf_nodes, start=1):
        node = g.nodes[node_key]
        if resume:
            existing_path = node.get("content_file_path") or ""
            if not existing_path:
                existing_path = str((slides_dir / f"{node_key}.md").resolve())
                node["content_file_path"] = existing_path
            if existing_path and Path(existing_path).exists():
                slide_title = node.get("title", "")
                print(f"[Slides] {idx}/{total_slides} {slide_title} (skip: exists)")
                try:
                    existing_md = Path(existing_path).read_text(encoding="utf-8", errors="ignore").strip()
                    if existing_md:
                        if disable_general_knowledge_citation:
                            sanitized = _strip_general_knowledge_markers(existing_md)
                            if sanitized != existing_md:
                                Path(existing_path).write_text(sanitized + "\n", encoding="utf-8")
                                existing_md = sanitized
                        _update_seen_lines(seen_lines, existing_md)
                        prev_slides = [{"title": slide_title, "content": existing_md}] + prev_slides
                        prev_slides = prev_slides[:3]
                        if generate_images and images_dir is not None:
                            slide_text = _strip_image_embeds(existing_md)
                            image_body = slide_text or node.get("summary", "") or slide_title
                            if image_body:
                                image_name = f"slide_{idx:02d}.png"
                                image_path = images_dir / image_name
                                if not image_path.exists():
                                    previous_summary = _summarize_previous_slides(prev_slides[1:])
                                    image_prompt = _build_image_prompt(
                                        presentation_title=title,
                                        presentation_style=style_guidance or "(none)",
                                        slide_title=slide_title,
                                        slide_body=image_body,
                                        previous_summary=previous_summary,
                                    )
                                    try:
                                        print(f"[Images] {idx}/{total_slides} {slide_title}")
                                        generate_slide_image_openrouter(
                                            image_prompt,
                                            str(image_path),
                                            model_name=image_model,
                                            out_dir=out_dir,
                                        )
                                    except Exception as exc:
                                        print(f"[WARN] Image generation failed for slide {idx}: {exc}")
                                if image_path.exists() and "![[images/" not in existing_md:
                                    updated = f"{existing_md.rstrip()}\n\n![[images/{image_name}]]\n"
                                    Path(existing_path).write_text(updated, encoding="utf-8")
                except Exception:
                    pass
                if progress_path is not None:
                    save_graph_json(g, progress_path)
                log_progress(completed=idx)
                continue
        print(f"[Slides] {idx}/{total_slides} {node.get('title','')}")

        previous_slides = ""
        if prev_slides:
            for prev_idx, item in enumerate(prev_slides, start=1):
                previous_slides += (
                    f"Slide {prev_idx} title: {item['title']}\n"
                    f"Slide {prev_idx} content:\n{item['content']}\n\n"
                )
        if not previous_slides:
            previous_slides = "(none)"

        query = f"{title}\n{summary}\n{node.get('title','')}\n{node.get('summary','')}"
        items = retrieval_manager.retrieve(
            query, k=retrieval_manager.default_k, diversify_sources=True, allow_web=False
        )
        retrieved_context = retrieval_manager.format_context(items, max_chars_total=6000) or "(none)"
        scientific_sources = "(none)"
        if citations_enabled and mcp_papers is not None and mcp_papers.is_available():
            mcp_items = mcp_papers.retrieve(query, k=retrieval_manager.default_k)
            scientific_sources = _format_scientific_sources(mcp_items)

        slide_md = writer.run(
            {
                "presentation_title": title,
                "presentation_summary": summary,
                "presentation_audience": audience or "(not specified)",
                "presentation_duration": duration_minutes,
                "presentation_style": style_guidance or "(none)",
                "outline_text": outline_text,
                "previous_slides": previous_slides,
                "retrieved_context": retrieved_context,
                "scientific_sources": scientific_sources,
                "citations_enabled": "true" if citations_enabled else "false",
                "disable_general_knowledge_citation": (
                    "true" if disable_general_knowledge_citation else "false"
                ),
                "node_key": node_key,
                "slide_title": node.get("title", ""),
                "slide_summary": node.get("summary", ""),
                "n_pages": node.get("n_pages", 1.0),
                "slide_draft": "",
            },
            agent_ctx,
        )

        if retrieval_manager.enable_web:
            draft_query = build_retrieval_query_from_tex(slide_md, max_chars=800)
            if draft_query:
                refined_query = f"{node.get('title','')}\n{draft_query}"
                refined_items = retrieval_manager.retrieve(
                    refined_query, k=retrieval_manager.default_k, diversify_sources=True, allow_web=True
                )
                merged_items = items + refined_items
                retrieved_context = retrieval_manager.format_context(merged_items, max_chars_total=6000)
                if retrieved_context:
                    slide_md = writer.run(
                        {
                            "presentation_title": title,
                            "presentation_summary": summary,
                            "presentation_audience": audience or "(not specified)",
                            "presentation_duration": duration_minutes,
                            "presentation_style": style_guidance or "(none)",
                            "outline_text": outline_text,
                            "previous_slides": previous_slides,
                            "retrieved_context": retrieved_context,
                            "scientific_sources": scientific_sources,
                            "citations_enabled": "true" if citations_enabled else "false",
                            "disable_general_knowledge_citation": (
                                "true" if disable_general_knowledge_citation else "false"
                            ),
                            "node_key": node_key,
                            "slide_title": node.get("title", ""),
                            "slide_summary": node.get("summary", ""),
                            "n_pages": node.get("n_pages", 1.0),
                            "slide_draft": slide_md,
                        },
                        agent_ctx,
                    )

        slide_md, _ = enforce_section_length(
            llm,
            slide_md,
            target_pages=float(node.get("n_pages", 1.0)),
            out_dir=out_dir,
            label="presentation_slide",
            lines_per_page=LINES_PER_SLIDE,
            tolerance=0.1,
        )
        slide_md = slide_md.strip()
        slide_md = _strip_kb_markers(slide_md)
        if disable_general_knowledge_citation:
            slide_md = _strip_general_knowledge_markers(slide_md)
        slide_md = _dedupe_slide_lines(slide_md, seen_lines)
        slide_md = _ensure_max_lines(slide_md, MAX_LINES_PER_SLIDE)

        if generate_images and images_dir is not None:
            image_name = f"slide_{idx:02d}.png"
            image_path = images_dir / image_name
            slide_text = _strip_image_embeds(slide_md)
            image_body = slide_text or node.get("summary", "") or node.get("title", "")
            if image_body:
                previous_summary = _summarize_previous_slides(prev_slides)
                image_prompt = _build_image_prompt(
                    presentation_title=title,
                    presentation_style=style_guidance or "(none)",
                    slide_title=node.get("title", ""),
                    slide_body=image_body,
                    previous_summary=previous_summary,
                )
                try:
                    print(f"[Images] {idx}/{total_slides} {node.get('title','')}")
                    generate_slide_image_openrouter(
                        image_prompt,
                        str(image_path),
                        model_name=image_model,
                        out_dir=out_dir,
                    )
                except Exception as exc:
                    print(f"[WARN] Image generation failed for slide {idx}: {exc}")

            if image_body and image_path.exists():
                slide_md = f"{slide_md.rstrip()}\n\n![[images/{image_name}]]"

        slide_path = slides_dir / f"{node_key}.md"
        slide_path.write_text(slide_md, encoding="utf-8")
        attach_content_path(g, node_key, slide_path)
        if progress_path is not None:
            save_graph_json(g, progress_path)

        prev_slides = [{"title": node.get("title", ""), "content": slide_md}] + prev_slides
        prev_slides = prev_slides[:3]
        processed_slides += 1
        log_progress(completed=idx)


def _build_presentation_markdown(g: nx.DiGraph, out_dir: Path) -> Path:
    title = g.graph.get("title", "Presentation")
    summary = g.graph.get("summary", "")
    theme = g.graph.get("theme", "Madrid")
    paginate = bool(g.graph.get("paginate", False))
    outline = bool(g.graph.get("outline", True))
    author = str(g.graph.get("author", "") or "").strip()
    header = str(g.graph.get("header", "") or "").strip()
    footer = str(g.graph.get("footer", "") or "").strip()

    lines: List[str] = []
    lines.append(f"theme: {theme}")
    lines.append(f"paginate: {'true' if paginate else 'false'}")
    lines.append(f"outline: {'true' if outline else 'false'}")
    if author:
        lines.append(f"author: {author}")
    if header:
        lines.append(f"header: {header}")
    if footer:
        lines.append(f"footer: {footer}")
    lines.append("")
    lines.append("---")
    lines.append("<!-- _class: title -->")
    lines.append(f"# {title}")
    if summary:
        lines.append("")
        lines.append(summary)
    if author:
        lines.append("")
        lines.append(f"**{author}**")

    def add_slide(node_key: str) -> None:
        node = g.nodes[node_key]
        path = node.get("content_file_path")
        body = ""
        if path:
            body = Path(path).read_text(encoding="utf-8", errors="ignore").strip()

        lines.append("")
        lines.append("---")
        lines.append(f"# {node.get('title','')}")
        if body:
            lines.append(body)

    for node_key in leaf_nodes_in_order(g):
        add_slide(node_key)

    md_filename = safe_filename(title) + ".md"
    md_path = out_dir / md_filename
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return md_path


def _generate_narration_items(
    llm: OpenRouterLLM,
    md_path: Path,
    *,
    model: Optional[str] = None,
    temperature: float = 0.7,
    out_dir: Path,
) -> List[Dict[str, Any]]:
    content = md_path.read_text(encoding="utf-8", errors="ignore")
    slides = split_presentation_markdown(content)
    system_prompt = get_prompt("presentation_narration_system")
    user_template = get_prompt("presentation_narration_user")
    narration_items: List[Dict[str, Any]] = []
    for slide in slides:
        slide_body = _strip_image_embeds(slide.body)
        user_prompt = render(
            user_template,
            slide_title=slide.title,
            slide_body=slide_body,
        )
        narration = llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=model,
            temperature=temperature,
            allow_tools=False,
        ).strip()
        usage = llm.get_last_usage()
        if usage:
            log_usage(out_dir, "presentation_narration", llm, usage, {"slide_index": slide.index})
        narration_items.append(
            {
                "index": slide.index,
                "title": slide.title,
                "narration": narration,
            }
        )
    return narration_items


def _write_narration_outputs(
    narration_items: List[Dict[str, Any]],
    out_dir: Path,
    base_name: str,
) -> Tuple[Path, Path]:
    json_path = out_dir / f"{base_name}_narration.json"
    md_path = out_dir / f"{base_name}_narration.md"

    json_path.write_text(
        json.dumps({"slides": narration_items}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md_lines: List[str] = ["# Narration Script"]
    for item in narration_items:
        title = item.get("title") or f"Slide {item.get('index')}"
        md_lines.append("")
        md_lines.append(f"## {title}")
        md_lines.append("")
        md_lines.append(item.get("narration", "").strip())
    md_path.write_text("\n".join(md_lines).rstrip() + "\n", encoding="utf-8")

    return json_path, md_path


def _generate_audio_tracks(
    narration_items: List[Dict[str, Any]],
    out_dir: Path,
    *,
    base_name: str,
    tts_mode: str,
    tts_model: Optional[str],
    exclude_slides: set[int],
) -> Tuple[List[Tuple[int, Path, float]], Optional[Path]]:
    try:
        from pydub import AudioSegment  # type: ignore
    except Exception as exc:
        raise RuntimeError("Missing optional dependency 'pydub'. Install: pip install pydub") from exc

    temp_dir = out_dir / "media_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    audio_items: List[Tuple[int, Path, float]] = []
    combined = AudioSegment.silent(duration=0)
    ext = "wav" if tts_mode == "local" else "mp3"

    for item in narration_items:
        slide_index = int(item.get("index", 0))
        if slide_index in exclude_slides:
            continue
        narration = str(item.get("narration", "")).strip()
        if not narration:
            continue
        audio_path = temp_dir / f"slide_{slide_index}.{ext}"
        if tts_mode == "local":
            synthesize_speech_local_tts(narration, str(audio_path), search_dir=str(out_dir))
        else:
            synthesize_speech_openrouter_tts(
                narration,
                str(audio_path),
                model_name=tts_model or DEFAULT_OPENROUTER_TTS_MODEL,
                out_dir=out_dir,
            )
        audio_segment = AudioSegment.from_file(audio_path)
        duration_sec = audio_segment.duration_seconds
        audio_items.append((slide_index, audio_path, duration_sec))
        combined += audio_segment

    if not audio_items:
        return [], None

    combined_path = out_dir / f"{base_name}_audio.wav"
    combined.export(combined_path, format="wav")
    return audio_items, combined_path


def _write_subtitles(
    narration_items: List[Dict[str, Any]],
    audio_items: List[Tuple[int, Path, float]],
    out_dir: Path,
    base_name: str,
) -> Path:
    durations = {idx: dur for idx, _, dur in audio_items}
    subtitle_lines: List[str] = []
    current_time = 0.0
    counter = 1
    for item in narration_items:
        slide_index = int(item.get("index", 0))
        narration = str(item.get("narration", "")).strip()
        duration = durations.get(slide_index)
        if duration is None:
            continue
        start_time = current_time
        end_time = current_time + duration
        subtitle_lines.append(
            f"{counter}\n"
            f"{format_srt_timestamp(start_time)} --> {format_srt_timestamp(end_time)}\n"
            f"{narration}\n\n"
        )
        counter += 1
        current_time = end_time

    srt_path = out_dir / f"{base_name}.srt"
    srt_path.write_text("".join(subtitle_lines), encoding="utf-8")
    return srt_path


def _write_run_meta(
    run_ctx: RunContext,
    args: Any,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    error: Optional[str],
    llm: Optional[OpenRouterLLM],
) -> None:
    models = {"primary": llm.config.model if llm is not None else None}
    token_totals = {"primary": llm.get_total_tokens() if llm is not None else 0}
    cost_totals = {"primary": llm.get_total_cost_usd() if llm is not None else None}
    write_run_meta(
        run_ctx=run_ctx,
        args=args,
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        error=error,
        models=models,
        token_totals=token_totals,
        cost_totals_usd=cost_totals,
    )


def run_presentation(args: Any, run_ctx: Optional[RunContext], logger: Any) -> int:
    started_at = datetime.now(timezone.utc)
    status = "ok"
    error: Optional[str] = None
    llm: Optional[OpenRouterLLM] = None

    try:
        input_path = Path(args.input).expanduser().resolve()
        out_dir = Path(args.out_dir).expanduser().resolve()
        json_path = Path(args.json_path).expanduser()
        if not json_path.is_absolute():
            json_path = out_dir / json_path
        json_path = json_path.resolve()

        prompts = load_presentation_prompts()
        set_prompt_registry("presentation", prompts)

        if run_ctx is None:
            run_ctx = RunContext.create(out_dir=out_dir, mode="presentation")
        progress_path = run_ctx.structure_graph_path

        if not input_path.exists():
            print(f"Error: input file not found: {input_path}", file=sys.stderr)
            return 2

        out_dir.mkdir(parents=True, exist_ok=True)
        json_path.parent.mkdir(parents=True, exist_ok=True)

        kb = None
        if args.kb_dir:
            kb_dir = Path(args.kb_dir).expanduser().resolve()
            print(f"[KB] Building knowledge base from: {kb_dir}")
            kb = KnowledgeBase.build_from_directory(
                kb_dir,
                cache_dir=out_dir / ".kb_cache",
                force_rebuild=bool(args.rebuild_kb),
            )
            print(f"[KB] Done. Chunks: {len(kb.chunks)}")
        else:
            print("[KB] --kb-dir not provided. RAG disabled.")

        citations_enabled = bool(getattr(args, "presentation_citations", False))
        tavily = None
        mcp_papers = None
        enable_web_rag = bool(getattr(args, "enable_web_rag", False))
        if enable_web_rag or citations_enabled:
            mcp_papers = MCPPaperRetriever()
        if enable_web_rag:
            api_key = os.environ.get("TAVILY_API_KEY")
            tavily = TavilyRetriever(api_key=api_key)
        retrieval_manager = RetrievalManager(
            local_kb=kb,
            mcp_papers=mcp_papers,
            tavily=tavily,
            enable_web=enable_web_rag,
            default_k=int(getattr(args, "web_rag_k", 5)),
        )
        if enable_web_rag:
            has_mcp = bool(mcp_papers and mcp_papers.is_available())
            has_tavily = bool(tavily and tavily.api_key)
            if not has_mcp and not has_tavily:
                print(
                    "[WARN] Web RAG enabled but no MCP paper tools or Tavily API key available; using KB only."
                )
            elif not has_mcp and has_tavily:
                print("[INFO] MCP paper tools unavailable; falling back to Tavily search.")
        if citations_enabled and not (mcp_papers and mcp_papers.is_available()):
            print(
                "[WARN] Presentation citations enabled but MCP paper tools unavailable; citations will be omitted."
            )

        llm = OpenRouterLLM()

        image_model = getattr(args, "presentation_image_model", None) or DEFAULT_IMAGE_MODEL
        generate_images = not bool(getattr(args, "no_image", False))
        disable_general_knowledge_citation = bool(
            getattr(args, "disable_general_knowledge_citation", False)
        )
        if not generate_images:
            print("[Images] Disabled (--no-image).")
        if disable_general_knowledge_citation:
            print(
                "[Slides] General-knowledge citation marker disabled "
                "(--disable-general-knowledge-citation)."
            )

        g = None
        if args.resume and progress_path.exists():
            print(f"[RESUME] Loading saved structure from: {progress_path}")
            g = load_graph_json(progress_path)
        else:
            t0 = time.perf_counter()
            presentation_json = None
            if json_path.exists():
                if args.use_txt:
                    choice = "t"
                elif args.use_json:
                    choice = "j"
                else:
                    choice = _ask_choice(
                        f"JSON already exists ({json_path.name}). Use existing JSON or regenerate from TXT?",
                        {"j": "json", "t": "txt"},
                        default="j",
                    )
                if choice == "j":
                    presentation_json = json.loads(_read_text(json_path))
                else:
                    txt_spec = _read_text(input_path)
                    presentation_json = _build_presentation_json(llm, txt_spec, kb=kb)
                    usage = llm.get_last_usage()
                    if usage:
                        log_usage(out_dir, "presentation_json", llm, usage, {"stage": "presentation_json"})
                    json_path.write_text(
                        json.dumps(presentation_json, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
            else:
                txt_spec = _read_text(input_path)
                presentation_json = _build_presentation_json(llm, txt_spec, kb=kb)
                usage = llm.get_last_usage()
                if usage:
                    log_usage(out_dir, "presentation_json", llm, usage, {"stage": "presentation_json"})
                json_path.write_text(
                    json.dumps(presentation_json, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            print(f"[JSON] Duration: {_format_duration(time.perf_counter() - t0)}")

            g = _build_graph_from_presentation_json(_normalize_presentation_json(presentation_json))
            _subdivide_presentation_graph(
                llm,
                g,
                kb=kb,
                retrieval_manager=retrieval_manager,
                progress_path=progress_path,
                fail_fast_schema=bool(getattr(args, "fail_fast_schema", False)),
            )
            save_graph_json(g, progress_path)

        if g is None:
            raise RuntimeError("Graph not initialized.")

        author = str(g.graph.get("author", "") or "").strip()
        if not author:
            author = _ask_text("Enter presenter name", required=False)
            g.graph["author"] = author
            save_graph_json(g, progress_path)

        if getattr(args, "no_md", False):
            print("[WARN] --no-md ignored in presentation mode (Markdown is required).")

        _generate_slide_contents(
            llm,
            g,
            out_dir=out_dir,
            kb=kb,
            retrieval_manager=retrieval_manager,
            progress_path=progress_path,
            resume=bool(args.resume),
            citations_enabled=citations_enabled,
            mcp_papers=mcp_papers,
            image_model=image_model,
            generate_images=generate_images,
            disable_general_knowledge_citation=disable_general_knowledge_citation,
        )

        md_path = _build_presentation_markdown(g, out_dir)
        outputs: List[Path] = [md_path]

        base_name = safe_filename(g.graph.get("title", "presentation"))

        export_pptx = bool(getattr(args, "presentation_pptx", False))
        export_tex = bool(getattr(args, "presentation_tex", False))
        want_video = bool(getattr(args, "presentation_video", False))
        want_narration = bool(getattr(args, "presentation_narration", False))
        want_tts = bool(getattr(args, "presentation_tts", False))

        if want_video:
            export_tex = True
            want_tts = True
            want_narration = True

        if export_pptx:
            pptx_path = md_to_pptx(md_path)
            outputs.append(pptx_path)

        tex_path = None
        pdf_path = None
        if export_tex:
            tex_path = md_to_beamer_tex(md_path)
            outputs.append(tex_path)
            force_pdf = bool(want_video)
            if getattr(args, "no_pdf", False) and force_pdf:
                print("[WARN] --no-pdf ignored because --presentation-video requires a PDF.")
            if not getattr(args, "no_pdf", False) or force_pdf:
                from autogenbook.presentation_export import compile_latex

                compile_latex(tex_path)
                pdf_path = tex_path.with_suffix(".pdf")
                outputs.append(pdf_path)

        narration_items: List[Dict[str, Any]] = []
        if want_narration or want_tts:
            narration_model = getattr(args, "presentation_narration_model", None)
            narration_items = _generate_narration_items(
                llm,
                md_path,
                model=narration_model,
                temperature=0.7,
                out_dir=out_dir,
            )
            _write_narration_outputs(narration_items, out_dir, base_name)

        audio_items: List[Tuple[int, Path, float]] = []
        if want_tts:
            exclude_slides = parse_slide_ranges(getattr(args, "presentation_exclude_slides", "") or "")
            tts_mode = str(getattr(args, "presentation_tts_mode", "openrouter"))
            tts_model = getattr(args, "presentation_tts_model", None)
            audio_items, combined_audio = _generate_audio_tracks(
                narration_items,
                out_dir,
                base_name=base_name,
                tts_mode=tts_mode,
                tts_model=tts_model,
                exclude_slides=exclude_slides,
            )
            if combined_audio is not None:
                outputs.append(combined_audio)

        if want_video:
            if pdf_path is None:
                raise RuntimeError("PDF not available for video generation. Enable --presentation-tex.")
            if not audio_items:
                raise RuntimeError("No audio generated; cannot render video.")

            content = md_path.read_text(encoding="utf-8", errors="ignore")
            meta = content.split("\n---\n", 1)[0]
            outline = True
            for line in meta.splitlines():
                match = re.match(r"^outline\s*:\s*(.+)$", line.strip(), flags=re.IGNORECASE)
                if match:
                    outline = match.group(1).strip().lower() in {"true", "yes", "1", "on"}
                    break

            def slide_index_map(idx: int) -> int:
                if not outline:
                    return idx
                return idx if idx == 1 else idx + 1

            srt_path = _write_subtitles(narration_items, audio_items, out_dir, base_name)
            outputs.append(srt_path)

            video_path = md_path.with_suffix(".mp4")
            render_video_from_pdf(
                pdf_path,
                audio_items,
                out_path=video_path,
                temp_dir=out_dir / "media_tmp",
                slide_index_map=slide_index_map,
            )
            outputs.append(video_path)

        print("\nDone. Outputs:")
        for path in outputs:
            print(f" - {path}")
        overall_usage = _extract_overall_usage_totals()
        est_costs = _estimate_cost_from_tokens(
            overall_usage.get("input_tokens"),
            overall_usage.get("output_tokens"),
            llm,
        )
        input_cost = overall_usage.get("input_cost_usd")
        output_cost = overall_usage.get("output_cost_usd")
        total_cost = overall_usage.get("total_cost_usd")
        input_est = False
        output_est = False
        total_est = False
        if input_cost is None:
            input_cost = est_costs.get("input_cost_usd")
            input_est = input_cost is not None
        if output_cost is None:
            output_cost = est_costs.get("output_cost_usd")
            output_est = output_cost is not None
        if total_cost is None:
            total_cost = est_costs.get("total_cost_usd")
            total_est = total_cost is not None

        print(
            "[TOKENS] IN={in_tok} OUT={out_tok} TOTAL={tot_tok}".format(
                in_tok=_format_int(overall_usage.get("input_tokens")),
                out_tok=_format_int(overall_usage.get("output_tokens")),
                tot_tok=_format_int(overall_usage.get("total_tokens")),
            )
        )
        print(
            "[COST] IN={in_cost} OUT={out_cost} TOTAL={total_cost}{cost_note}".format(
                in_cost=_format_cost(input_cost, estimated=input_est),
                out_cost=_format_cost(output_cost, estimated=output_est),
                total_cost=_format_cost(total_cost, estimated=total_est),
                cost_note=" (est)" if input_est or output_est or total_est else "",
            )
        )

        return 0
    except Exception as exc:
        status = "error"
        error = str(exc)
        raise
    finally:
        finished_at = datetime.now(timezone.utc)
        if run_ctx is not None:
            _write_run_meta(run_ctx, args, started_at, finished_at, status, error, llm)
