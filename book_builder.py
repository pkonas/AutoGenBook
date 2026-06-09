
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx
from pylatex import (
    Command,
    Document,
    Package,
    Section,
    Subsection,
    Subsubsection,
)
from pylatex.section import Chapter, Paragraph, Subparagraph
from pylatex.utils import NoEscape, escape_latex

from openrouter_llm import OpenRouterLLM
from autogenbook.prompts.agent_prompts import render
from autogenbook.prompts.registry import get_prompt
from rag_kb import KnowledgeBase
from autogenbook.agents.base import AgentContext
from autogenbook.agents import (
    BookSectionWriterAgent,
    BookSectionReviewerAgent,
    BookSectionRevisionAgent,
    ContextMemoryAgent,
    StructureSubdividerAgent,
)
from autogenbook.citations.extract import extract_citations
from autogenbook.graph.doc_graph import (
    attach_content_path,
    leaf_nodes_in_order,
    load_graph_json,
    save_graph_json,
    sort_node_keys,
)
from autogenbook.length_control import enforce_section_length
from autogenbook.memory.context_memory import ContextMemory
from autogenbook.retrieval.manager import RetrievalManager
from autogenbook.retrieval.kb_citations import extract_page, extract_slide, kb_cite_key
from utils import (
    build_retrieval_query_from_tex,
    clean_markdown_content,
    convert_lstlisting_to_markdown,
    extract_first_json_object,
    extract_tex_fence,
    extract_markdown_fence,
    generate_outline_text,
    get_depth,
    get_equation_frequency_prompt,
    normalize_markdown_paragraphs,
    safe_filename,
    ensure_robustness_preamble,
    ensure_bool,
    ensure_float,
    ensure_int,
)


@dataclass
class AppConfig:
    # content format
    content_format: str = "markdown"

    # output controls
    tex_output: bool = True
    pdf_output: bool = True
    md_output: bool = True

    # generation controls
    equation_frequency_level: int = 4
    do_consider_outline: bool = True
    do_consider_previous_sections: bool = True
    n_previous_sections: int = 1
    enable_section_review: bool = True
    section_revision_passes: int = 1

    # structure controls
    max_depth: int = 5
    max_output_pages: float = 1.5

    # rag controls
    rag_top_k: int = 6
    rag_max_chars_total: int = 6000


_PAGE_COUNT_RE = re.compile(
    r"\((?:~\s*)?(?P<pages>\d+(?:[.,]\d+)?)\s*(?:page|pages|strana|strany|stran|slides?)?\)\s*$",
    re.IGNORECASE,
)
_ROOT_PAGE_HINT_RE = re.compile(
    r"(?:overall|overal|celkov[ýaé]|rozsah).*?(?P<pages>\d+(?:[.,]\d+)?)\s*pages?",
    re.IGNORECASE,
)


def _normalize_outline_title(raw_title: str) -> Tuple[str, Optional[float]]:
    title = str(raw_title or "").strip()
    n_pages: Optional[float] = None
    match = _PAGE_COUNT_RE.search(title)
    if match:
        n_pages = ensure_float(match.group("pages").replace(",", "."), 0.0)
        title = title[: match.start()].rstrip()
    title = re.sub(r"^[*_`\s]+|[*_`\s]+$", "", title).strip()
    title = re.sub(
        r"^(?:chapter|kapitola|section)\s+\d+(?:\.\d+)*\s*[:.\-]\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"^\d+(?:\.\d+)*\s*(?:[:.\-]\s*|\s+)", "", title).strip()
    return title, n_pages


def _summarize_outline_body(body_lines: List[str]) -> str:
    cleaned: List[str] = []
    in_fence = False
    skip_sources = False
    for raw_line in body_lines:
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not stripped or stripped == "---":
            continue
        if not in_fence and re.match(
            r"^[*_`\s]*(?:sources?|references?|literature)\s*:?[ *_`\s]*$",
            stripped,
            flags=re.IGNORECASE,
        ):
            skip_sources = True
            continue
        if skip_sources:
            continue
        if stripped.lower().startswith(("http://", "https://")):
            continue
        line = re.sub(r"^[\-\*\+\u2022>\s]+", "", stripped)
        line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
        line = re.sub(r"\*(.*?)\*", r"\1", line)
        line = line.replace("`", "")
        line = re.sub(r"\s+", " ", line).strip()
        lowered = line.lower()
        if lowered in {
            "content:",
            "framework:",
            "format:",
            "source:",
            "sources:",
            "reference:",
            "references:",
            "literature:",
            "investigated pat:",
            "cfd set-up",
            "cfd set-up:",
            "measurements",
            "measurements:",
            "geometry",
            "geometry:",
            "mesh",
            "mesh:",
        }:
            continue
        if line:
            cleaned.append(line)
    summary = " ".join(cleaned).strip()
    if len(summary) > 900:
        summary = summary[:897].rstrip() + "..."
    return summary


def _extract_root_page_hint(txt_spec: str) -> Optional[float]:
    for line in txt_spec.splitlines():
        match = _ROOT_PAGE_HINT_RE.search(line)
        if match:
            return ensure_float(match.group("pages").replace(",", "."), 0.0)
    return None


def _finalize_outline_node(node: Dict[str, Any]) -> Dict[str, Any]:
    child_nodes = [_finalize_outline_node(ch) for ch in node.get("childs", []) if isinstance(ch, dict)]
    n_pages = node.get("n_pages")
    if n_pages is None and child_nodes:
        n_pages = round(sum(float(ch.get("n_pages", 0.0)) for ch in child_nodes), 1)
    if n_pages is None:
        n_pages = 1.0
    summary = _summarize_outline_body(node.get("_body_lines", []))
    out: Dict[str, Any] = {
        "title": str(node.get("title", "")).strip(),
        "summary": summary,
        "n_pages": ensure_float(n_pages, 1.0),
        "needsSubdivision": bool(child_nodes),
        "structure_locked": True,
    }
    if child_nodes:
        out["childs"] = child_nodes
    return out


def _extract_explicit_outline_from_txt(txt_spec: str) -> List[Dict[str, Any]]:
    roots: List[Dict[str, Any]] = []
    stack: List[Tuple[int, Dict[str, Any]]] = []
    in_fence = False

    for line in txt_spec.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            if stack:
                stack[-1][1].setdefault("_body_lines", []).append(line)
            continue

        heading_match = None if in_fence else re.match(r"^(#{2,6})\s+(.+?)\s*$", line)
        if heading_match:
            level = len(heading_match.group(1))
            title, n_pages = _normalize_outline_title(heading_match.group(2))
            if not title:
                continue
            node: Dict[str, Any] = {
                "title": title,
                "n_pages": n_pages,
                "childs": [],
                "_body_lines": [],
            }
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                stack[-1][1].setdefault("childs", []).append(node)
            else:
                roots.append(node)
            stack.append((level, node))
            continue

        if stack:
            stack[-1][1].setdefault("_body_lines", []).append(line)

    return [_finalize_outline_node(node) for node in roots if str(node.get("title", "")).strip()]


def _normalize_book_child(raw: Dict[str, Any]) -> Dict[str, Any]:
    childs = raw.get("childs", [])
    if not isinstance(childs, list):
        childs = []
    norm_childs = []
    for ch in childs:
        if not isinstance(ch, dict):
            continue
        norm_childs.append(_normalize_book_child(ch))

    out = {
        "title": str(raw.get("title", "")).strip(),
        "summary": str(raw.get("summary", "")).strip(),
        "n_pages": ensure_float(raw.get("n_pages", 1.0), 1.0),
        "needsSubdivision": ensure_bool(raw.get("needsSubdivision", bool(norm_childs)), bool(norm_childs)),
    }
    if norm_childs:
        out["childs"] = norm_childs
        out["needsSubdivision"] = True
    if ensure_bool(raw.get("structure_locked", False), False):
        out["structure_locked"] = True
    return out


def _normalize_book_json(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fill defaults and sanitize types.
    """
    out = dict(raw)

    out["title"] = str(out.get("title", "")).strip() or "Untitled Book"
    out["summary"] = str(out.get("summary", "")).strip()
    out["n_pages"] = ensure_float(out.get("n_pages", 40), 40)

    out["target_readers"] = str(out.get("target_readers", "")).strip()
    out["additional_requirements"] = str(out.get("additional_requirements", "")).strip()

    out["equation_frequency_level"] = ensure_int(out.get("equation_frequency_level", 4), 4)
    out["equation_frequency_level"] = max(1, min(5, out["equation_frequency_level"]))

    out["do_consider_outline"] = ensure_bool(out.get("do_consider_outline", True), True)
    out["do_consider_previous_sections"] = ensure_bool(out.get("do_consider_previous_sections", True), True)

    out["max_depth"] = ensure_int(out.get("max_depth", 5), 5)
    out["max_output_pages"] = ensure_float(out.get("max_output_pages", 1.5), 1.5)

    childs = out.get("childs", [])
    if not isinstance(childs, list):
        childs = []
    norm_childs = []
    for ch in childs:
        if not isinstance(ch, dict):
            continue
        norm_childs.append(_normalize_book_child(ch))
    out["childs"] = norm_childs
    return out


def generate_book_json_from_txt(
    llm: OpenRouterLLM,
    txt_spec: str,
    kb: Optional[KnowledgeBase] = None,
    rag_top_k: int = 6,
    rag_max_chars_total: int = 6000,
) -> Dict[str, Any]:
    print("[JSON] Generuji strukturu knihy z TXT...")
    kb_context = ""
    if kb is not None:
        kb_context = kb.format_context(txt_spec, k=rag_top_k, max_chars_total=rag_max_chars_total)

    print("[JSON] Odesílám požadavek do LLM...")
    prompt_template = get_prompt("book_json_from_txt_user")
    prompt = render(prompt_template, txt_spec=txt_spec, kb_context=kb_context)
    content = llm.chat(
        [
            {"role": "system", "content": get_prompt("book_json_from_txt_system")},
            {"role": "user", "content": prompt},
        ],
        allow_tools=False,
    )
    usage = llm.get_last_usage()
    if usage:
        print(llm.format_usage_line(usage, label="json_from_txt"))
    print("[JSON] Odpověď přijata, parsování JSON...")
    raw = extract_first_json_object(content)
    book_json = _normalize_book_json(raw)
    explicit_outline = _extract_explicit_outline_from_txt(txt_spec)
    if explicit_outline:
        book_json["childs"] = explicit_outline
        root_page_hint = _extract_root_page_hint(txt_spec)
        if root_page_hint:
            book_json["n_pages"] = root_page_hint
    return _normalize_book_json(book_json)


def build_graph_from_book_json(book_json: Dict[str, Any]) -> nx.DiGraph:
    g = nx.DiGraph()
    g.graph.update(
        {
            "title": book_json["title"],
            "summary": book_json.get("summary", ""),
            "n_pages": book_json.get("n_pages", 40),
            "target_readers": book_json.get("target_readers", ""),
            "additional_requirements": book_json.get("additional_requirements", ""),
            "equation_frequency_level": book_json.get("equation_frequency_level", 4),
            "do_consider_outline": book_json.get("do_consider_outline", True),
            "do_consider_previous_sections": book_json.get("do_consider_previous_sections", True),
            "max_depth": book_json.get("max_depth", 5),
            "max_output_pages": book_json.get("max_output_pages", 1.5),
        }
    )

    g.add_node(
        "book",
        title=book_json["title"],
        summary=book_json.get("summary", ""),
        n_pages=book_json.get("n_pages", 40),
        needsSubdivision=bool(book_json.get("childs")),
    )

    def add_children(parent_key: str, children: List[Dict[str, Any]]) -> None:
        for i, ch in enumerate(children, start=1):
            node_key = str(i) if parent_key == "book" else f"{parent_key}-{i}"
            attrs = {
                "title": str(ch.get("title", "")).strip(),
                "summary": str(ch.get("summary", "")).strip(),
                "n_pages": ensure_float(ch.get("n_pages", 1.0), 1.0),
                "needsSubdivision": ensure_bool(ch.get("needsSubdivision", False), False),
            }
            if ensure_bool(ch.get("structure_locked", False), False):
                attrs["structure_locked"] = True
            g.add_node(node_key, **attrs)
            g.add_edge(parent_key, node_key)
            nested = ch.get("childs", [])
            if isinstance(nested, list) and nested:
                add_children(node_key, [child for child in nested if isinstance(child, dict)])

    add_children("book", [child for child in book_json.get("childs", []) if isinstance(child, dict)])

    return g


def _node_children_sorted(g: nx.DiGraph, node: str) -> List[str]:
    children = list(g.successors(node))
    return sort_node_keys(children)


def _is_leaf(g: nx.DiGraph, node: str) -> bool:
    return g.out_degree(node) == 0


def _dedupe_items(items: List[Any]) -> List[Any]:
    seen = set()
    deduped: List[Any] = []
    for item in items:
        key = getattr(item, "cite_key", None) or getattr(item, "rid", None) or id(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _item_keys(items: List[Any]) -> set[str]:
    keys: set[str] = set()
    for item in items:
        key = getattr(item, "cite_key", None) or getattr(item, "rid", None)
        if key:
            keys.add(str(key))
    return keys


def subdivide_graph(
    llm: OpenRouterLLM,
    g: nx.DiGraph,
    kb: Optional[KnowledgeBase],
    rag_top_k: int,
    rag_max_chars_total: int,
    retrieval_manager: Optional[RetrievalManager] = None,
    progress_path: Optional[Path] = None,
) -> None:
    """
    Recursively subdivide nodes according to needsSubdivision/max_output_pages and max_depth.
    """
    max_depth = int(g.graph.get("max_depth", 5))
    max_output_pages = float(g.graph.get("max_output_pages", 1.5))

    book_title = g.nodes["book"].get("title", "")
    book_summary = g.nodes["book"].get("summary", "")
    target_readers = g.graph.get("target_readers", "")
    if retrieval_manager is None:
        retrieval_manager = RetrievalManager(
            local_kb=kb,
            default_k=rag_top_k,
            max_chars_total=rag_max_chars_total,
        )
    subdivider = StructureSubdividerAgent()
    run_id = time.strftime("%Y%m%dT%H%M%SZ")
    out_dir = progress_path.parent if progress_path is not None else Path.cwd()
    agent_ctx = AgentContext(
        run_id=run_id,
        out_dir=out_dir,
        kb=kb,
        llm=llm,
        mode="book",
        retrieval_manager=retrieval_manager,
    )

    # BFS by depth
    frontier = ["book"]
    print("[SUBDIVIDE] Zahajuji subdivizi uzlů...")
    start_time = time.perf_counter()
    total_candidates = 0
    for n in g.nodes:
        if n == "book":
            continue
        node = g.nodes[n]
        if _node_children_sorted(g, n):
            continue
        if bool(node.get("structure_locked", False)):
            continue
        needs_sub = bool(node.get("needsSubdivision", False))
        n_pages = float(node.get("n_pages", 1.0))
        should_subdivide = (needs_sub or n_pages >= max_output_pages)
        if should_subdivide:
            total_candidates += 1
    processed = 0

    def _format_eta(seconds: float) -> str:
        seconds = max(0, int(seconds))
        mins, sec = divmod(seconds, 60)
        hrs, mins = divmod(mins, 60)
        if hrs:
            return f"{hrs}h {mins}m {sec}s"
        if mins:
            return f"{mins}m {sec}s"
        return f"{sec}s"

    def _format_cost(cost: Optional[float]) -> str:
        if cost is None:
            return "n/a"
        return f"${cost:.6f}"

    for depth in range(1, max_depth + 1):
        next_frontier: List[str] = []
        for parent in frontier:
            for child in _node_children_sorted(g, parent):
                node = g.nodes[child]
                if _node_children_sorted(g, child):
                    next_frontier.append(child)
                    continue
                if bool(node.get("structure_locked", False)):
                    continue
                needs_sub = bool(node.get("needsSubdivision", False))
                n_pages = float(node.get("n_pages", 1.0))

                should_subdivide = (needs_sub or n_pages >= max_output_pages) and depth < max_depth
                if not should_subdivide:
                    continue

                processed += 1
                eta = 0.0
                if processed > 0:
                    avg = (time.perf_counter() - start_time) / processed
                    eta = avg * max(0, total_candidates - processed)
                print(
                    f"[SUBDIVIDE] Hloubka {depth}/{max_depth}: '{node.get('title','')}'"
                    f" | ETA { _format_eta(eta) }"
                )
                query = f"{book_title}\n{book_summary}\n{node.get('title','')}\n{node.get('summary','')}"
                retrieved_context = ""
                if retrieval_manager is not None:
                    items = retrieval_manager.retrieve(query, k=rag_top_k, allow_web=False)
                    retrieved_context = retrieval_manager.format_context(items, max_chars_total=rag_max_chars_total)
                if not retrieved_context:
                    retrieved_context = "(none)"

                max_attempts = 3
                last_err: Optional[Exception] = None
                section_list = None
                for attempt in range(1, max_attempts + 1):
                    print(f"[SUBDIVIDE] Generuji podsekce (pokus {attempt}/{max_attempts})...")
                    try:
                        section_list = subdivider.run(
                            {
                                "doc_kind": "book",
                                "doc_title": book_title,
                                "doc_summary": book_summary,
                                "target_audience": target_readers,
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
                        last_err = exc
                        if attempt == max_attempts:
                            raise ValueError(
                                f"Failed to parse section list after {max_attempts} attempts."
                            ) from exc
                        continue

                # remove existing children if any (we are re-subdividing)
                for old in list(g.successors(child)):
                    g.remove_node(old)

                for i, sub in enumerate(section_list, start=1):
                    sub_title = str(sub.get("title", "")).strip()
                    sub_summary = str(sub.get("summary", "")).strip()
                    sub_pages = ensure_float(sub.get("n_pages", max(0.2, n_pages / max(1, len(section_list)))), 0.5)
                    sub_pages = max(0.1, sub_pages)
                    sub_needs = ensure_bool(sub.get("needsSubdivision", sub_pages >= max_output_pages), sub_pages >= max_output_pages)

                    sub_key = f"{child}-{i}"
                    g.add_node(
                        sub_key,
                        title=sub_title,
                        summary=sub_summary,
                        n_pages=sub_pages,
                        needsSubdivision=sub_needs,
                        structure_locked=False,
                    )
                    g.add_edge(child, sub_key)

                next_frontier.append(child)
                if progress_path is not None:
                    save_graph_json(g, progress_path)

        if not next_frontier:
            break
        frontier = next_frontier


def generate_contents(
    llm: OpenRouterLLM,
    g: nx.DiGraph,
    out_dir: Path,
    kb: Optional[KnowledgeBase],
    cfg: AppConfig,
    retrieval_manager: Optional[RetrievalManager] = None,
    progress_path: Optional[Path] = None,
    resume: bool = False,
) -> None:
    """
    Generate section content for each leaf node and write to files.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    sections_dir = out_dir / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    content_format = str(getattr(cfg, "content_format", "latex") or "latex").strip().lower()
    if content_format not in {"latex", "markdown"}:
        content_format = "latex"
    section_ext = ".md" if content_format == "markdown" else ".tex"

    def _normalize_section_output(text: str) -> str:
        if content_format == "markdown":
            return extract_markdown_fence(text)
        return extract_tex_fence(text)

    book_title = g.nodes["book"].get("title", "")
    book_summary = g.nodes["book"].get("summary", "")
    book_pages = g.nodes["book"].get("n_pages", g.graph.get("n_pages", 40))
    target_readers = g.graph.get("target_readers", "")
    additional_requirements = g.graph.get("additional_requirements", "")

    toc_and_summary = ""
    if cfg.do_consider_outline:
        tree = build_tree_snapshot(g)
        toc_and_summary = generate_outline_text(tree, root="book")
    if not toc_and_summary:
        toc_and_summary = "(outline not provided)"

    n_prev = cfg.n_previous_sections if cfg.do_consider_previous_sections else 0
    prev_list: List[Dict[str, str]] = [{"title": "", "content": ""} for _ in range(n_prev)]
    run_id = time.strftime("%Y%m%dT%H%M%SZ")
    if retrieval_manager is None:
        retrieval_manager = RetrievalManager(
            local_kb=kb,
            default_k=cfg.rag_top_k,
            max_chars_total=cfg.rag_max_chars_total,
        )
    agent_ctx = AgentContext(
        run_id=run_id,
        out_dir=out_dir,
        kb=kb,
        llm=llm,
        mode="book",
        retrieval_manager=retrieval_manager,
    )
    writer = BookSectionWriterAgent(llm)
    reviewer = BookSectionReviewerAgent(llm)
    reviser = BookSectionRevisionAgent(llm)
    memory_agent = ContextMemoryAgent(llm)
    memory_path = out_dir / "context_memory.json"
    context_memory = ContextMemory.load(memory_path)

    nodes = leaf_nodes_in_order(g)
    total = len(nodes)
    completed = 0
    generated = 0
    gen_elapsed = 0.0
    def _format_eta(seconds: float) -> str:
        seconds = max(0, int(seconds))
        mins, sec = divmod(seconds, 60)
        hrs, mins = divmod(mins, 60)
        if hrs:
            return f"{hrs}h {mins}m {sec}s"
        if mins:
            return f"{mins}m {sec}s"
        return f"{sec}s"

    def _format_cost(cost: Optional[float]) -> str:
        if cost is None:
            return "n/a"
        return f"${cost:.6f}"

    for node_key in nodes:
        node = g.nodes[node_key]
        completed += 1

        existing_path = node.get("content_file_path")
        if resume:
            if existing_path and Path(existing_path).suffix.lower() != section_ext:
                existing_path = ""
                node["content_file_path"] = ""
            if not existing_path:
                existing_path = str((sections_dir / f"{node_key}{section_ext}").resolve())
                node["content_file_path"] = existing_path
            if existing_path and Path(existing_path).exists():
                eta = 0.0
                if generated > 0:
                    avg = gen_elapsed / max(1, generated)
                    eta = avg * max(0, total - completed)
                print(
                    f"[GEN] {completed}/{total} Skip existing section '{node.get('title','')}'"
                    f" | ETA { _format_eta(eta) }"
                )
                if progress_path is not None:
                    save_graph_json(g, progress_path)
                continue

        previous_sections = ""
        if n_prev:
            for i, item in enumerate(prev_list):
                if not item["title"]:
                    continue
                previous_sections += (
                    f"The title of part {i+1} sections ago is {item['title']}, and the content is as follows.\n"
                    f"{item['content']}\n\n"
                )
        if not previous_sections:
            previous_sections = "(none)"

        retrieved_context = ""
        query = f"{book_title}\n{book_summary}\n{node.get('title','')}\n{node.get('summary','')}"
        if retrieval_manager is not None:
            items = retrieval_manager.retrieve(
                query,
                k=cfg.rag_top_k,
                diversify_sources=True,
                allow_web=False,
            )
            retrieved_context = retrieval_manager.format_context(items, max_chars_total=cfg.rag_max_chars_total)
        if not retrieved_context:
            retrieved_context = "(none)"
        context_memory_excerpt = context_memory.summarize_excerpt(max_chars=2000)

        start = time.perf_counter()
        tex = writer.run(
            {
                "book_title": book_title,
                "book_summary": book_summary,
                "target_readers": target_readers or "(not specified)",
                "additional_requirements": additional_requirements or "(none)",
                "equation_frequency": get_equation_frequency_prompt(
                    int(g.graph.get("equation_frequency_level", cfg.equation_frequency_level))
                ),
                "toc_and_summary": toc_and_summary,
                "previous_sections": previous_sections,
                "context_memory_excerpt": context_memory_excerpt,
                "retrieved_context": retrieved_context,
                "node_key": node_key,
                "section_title": node.get("title", ""),
                "section_summary": node.get("summary", ""),
                "n_pages": node.get("n_pages", 1.0),
                "section_draft": "",
            },
            agent_ctx,
        )
        tex = _normalize_section_output(tex)
        if content_format == "markdown":
            tex = normalize_markdown_paragraphs(tex).strip()
        if getattr(retrieval_manager, "enable_web", False):
            draft_query = build_retrieval_query_from_tex(tex, max_chars=1200)
            if draft_query:
                refined_query = f"{node.get('title','')}\n{draft_query}"
                refined_items = retrieval_manager.retrieve(
                    refined_query, k=cfg.rag_top_k, diversify_sources=True, allow_web=True
                )
                merged_items = _dedupe_items(items + refined_items)
                if _item_keys(merged_items) != _item_keys(items):
                    items = merged_items
                    retrieved_context = retrieval_manager.format_context(
                        items, max_chars_total=cfg.rag_max_chars_total
                    )
                    if not retrieved_context:
                        retrieved_context = "(none)"
                    tex = writer.run(
                        {
                            "book_title": book_title,
                            "book_summary": book_summary,
                            "target_readers": target_readers or "(not specified)",
                            "additional_requirements": additional_requirements or "(none)",
                            "equation_frequency": get_equation_frequency_prompt(
                                int(
                                    g.graph.get(
                                        "equation_frequency_level", cfg.equation_frequency_level
                                    )
                                )
                            ),
                            "toc_and_summary": toc_and_summary,
                            "previous_sections": previous_sections,
                            "context_memory_excerpt": context_memory_excerpt,
                            "retrieved_context": retrieved_context,
                            "node_key": node_key,
                            "section_title": node.get("title", ""),
                            "section_summary": node.get("summary", ""),
                            "n_pages": node.get("n_pages", 1.0),
                            "section_draft": tex,
                        },
                        agent_ctx,
                    )
                    tex = _normalize_section_output(tex)
                    if content_format == "markdown":
                        tex = normalize_markdown_paragraphs(tex).strip()
        gen_elapsed += time.perf_counter() - start
        generated += 1

        review: Dict[str, Any] = {}
        if cfg.enable_section_review:
            review = reviewer.run(
                {
                    "section_title": node.get("title", ""),
                    "node_key": node_key,
                    "section_tex": tex,
                    "previous_sections": previous_sections,
                    "context_memory": context_memory_excerpt,
                    "toc_and_summary": toc_and_summary,
                    "retrieved_context": retrieved_context,
                },
                agent_ctx,
            )
            reviews_dir = out_dir / "section_reviews"
            reviews_dir.mkdir(parents=True, exist_ok=True)
            (reviews_dir / f"{node_key}.json").write_text(
                json.dumps(review, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            if not bool(review.get("ok_to_keep", True)) and cfg.section_revision_passes > 0:
                for _ in range(cfg.section_revision_passes):
                    revised = reviser.run(
                        {
                            "section_title": node.get("title", ""),
                            "node_key": node_key,
                            "section_tex": tex,
                            "review": review,
                            "previous_sections": previous_sections,
                            "toc_and_summary": toc_and_summary,
                            "context_memory": context_memory_excerpt,
                            "retrieved_context": retrieved_context,
                        },
                        agent_ctx,
                    )
                    revised_tex = _normalize_section_output(revised)
                    if content_format == "markdown":
                        revised_tex = normalize_markdown_paragraphs(revised_tex).strip()
                    if content_format == "latex":
                        revised_tex, _ = _sanitize_section_tex(revised_tex)
                    tex = revised_tex
                    review = reviewer.run(
                        {
                            "section_title": node.get("title", ""),
                            "node_key": node_key,
                            "section_tex": tex,
                            "previous_sections": previous_sections,
                            "context_memory": context_memory_excerpt,
                            "toc_and_summary": toc_and_summary,
                            "retrieved_context": retrieved_context,
                        },
                        agent_ctx,
                    )
                    if bool(review.get("ok_to_keep", True)):
                        break
                (reviews_dir / f"{node_key}_revised.json").write_text(
                    json.dumps(review, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

        tex, _ = enforce_section_length(
            llm,
            tex,
            target_pages=float(node.get("n_pages", 1.0)),
            out_dir=out_dir,
            label="book_section",
        )
        if content_format == "latex":
            tex, _ = _sanitize_section_tex(tex)
        else:
            tex = normalize_markdown_paragraphs(tex).strip()

        memory_payload = {
            "terms": context_memory.terms,
            "citations": context_memory.citations,
            "figures": context_memory.figures,
            "tables": context_memory.tables,
            "open_threads": context_memory.open_threads,
        }
        memory_update = memory_agent.run(
            {
                "context_memory_json": json.dumps(memory_payload, ensure_ascii=False, indent=2),
                "section_title": node.get("title", ""),
                "node_key": node_key,
                "section_latex": tex,
                "book_outline_text": toc_and_summary,
                "retrieved_context": retrieved_context,
            },
            agent_ctx,
        )
        context_memory.apply_agent_update(memory_update, node_key, node.get("title", ""))
        context_memory.save(memory_path)

        # Persist section content
        section_path = sections_dir / f"{node_key}{section_ext}"
        section_path.write_text(tex, encoding="utf-8")
        attach_content_path(g, node_key, section_path)
        if progress_path is not None:
            save_graph_json(g, progress_path)
        eta = 0.0
        if generated > 0:
            avg = gen_elapsed / max(1, generated)
            eta = avg * max(0, total - completed)
        print(
            f"[GEN] {completed}/{total} Generated section '{node.get('title','')}'"
            f" | ETA { _format_eta(eta) }"
        )

        # Update previous sections
        if n_prev:
            prev_list = [{"title": node.get("title", ""), "content": tex}] + prev_list
            prev_list = prev_list[:n_prev]


def build_tree_snapshot(g: nx.DiGraph) -> Dict[str, Dict[str, Any]]:
    """
    Convert networkx graph to a serializable tree-like dict.
    """
    tree: Dict[str, Dict[str, Any]] = {}
    for n in g.nodes:
        tree[n] = {
            "title": g.nodes[n].get("title", ""),
            "summary": g.nodes[n].get("summary", ""),
            "n_pages": g.nodes[n].get("n_pages", ""),
            "children": _node_children_sorted(g, n),
        }
    return tree


def build_latex_document(g: nx.DiGraph, out_dir: Path, kb: Optional[KnowledgeBase] = None) -> Path:
    """
    Assemble LaTeX .tex file from the graph and generated section files.
    Returns path to generated .tex file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _preprocess_section_files(g)

    geometry_options = {"a4paper": True, "margin": "25mm"}
    doc = Document(documentclass="report", geometry_options=geometry_options)

    # Preamble
    doc.packages.append(Package("amsmath"))
    doc.packages.append(Package("amssymb"))
    doc.packages.append(Package("amsfonts"))
    doc.packages.append(Package("mathtools"))
    doc.packages.append(Package("bm"))
    doc.packages.append(Package("physics"))
    doc.packages.append(Package("graphicx"))
    doc.packages.append(Package("microtype"))
    doc.packages.append(Package("iftex"))
    doc.preamble.append(
        NoEscape(
            r"\ifLuaTeX"
            r"\usepackage{fontspec}"
            r"\usepackage{unicode-math}"
            r"\else"
            r"\usepackage[utf8]{inputenc}"
            r"\usepackage[T1]{fontenc}"
            r"\fi"
            r"\usepackage[czech]{babel}"
        )
    )
    doc.preamble.append(NoEscape("\\shorthandoff{\"}"))
    doc.packages.append(Package("textcomp"))
    doc.packages.append(Package("listings"))
    doc.packages.append(Package("listingsutf8"))
    doc.packages.append(Package("xcolor"))
    doc.packages.append(Package("underscore", options="strings"))
    doc.packages.append(Package("url"))
    doc.packages.append(Package("xurl"))
    doc.packages.append(Package("hyperref"))
    doc.packages.append(Package("bookmark"))
    doc.packages.append(Package("natbib", options="square,numbers"))
    doc.preamble.append(NoEscape(r"\graphicspath{{figures/}}"))
    doc.preamble.append(NoEscape(r"\providecommand{\tightlist}{}"))
    doc.preamble.append(NoEscape(r"\renewcommand{\bibname}{Použitá literatura}"))
    doc.preamble.append(
        NoEscape(
            r"\renewcommand{\bibsection}{\chapter*{\bibname}\addcontentsline{toc}{chapter}{\bibname}}"
        )
    )
    ensure_robustness_preamble(out_dir)
    doc.preamble.append(NoEscape(r"\input{robustness.tex}"))

    def _sanitize_heading_text(text: str) -> NoEscape:
        cleaned = text.replace("\r", " ").replace("\n", " ").strip()
        cleaned = _normalize_unicode_math_symbols(cleaned)
        heading_math_aliases = {
            r"\nabla": "nabla",
            r"\partial": "partial",
            r"\Gamma": "Gamma",
            r"\Delta": "Delta",
            r"\Theta": "Theta",
            r"\Lambda": "Lambda",
            r"\Sigma": "Sigma",
            r"\Phi": "Phi",
            r"\Psi": "Psi",
            r"\Omega": "Omega",
            r"\alpha": "alpha",
            r"\beta": "beta",
            r"\gamma": "gamma",
            r"\delta": "delta",
            r"\epsilon": "epsilon",
            r"\theta": "theta",
            r"\lambda": "lambda",
            r"\mu": "mu",
            r"\nu": "nu",
            r"\xi": "xi",
            r"\pi": "pi",
            r"\rho": "rho",
            r"\sigma": "sigma",
            r"\phi": "phi",
            r"\varphi": "phi",
            r"\psi": "psi",
            r"\omega": "omega",
            r"\cdot": " dot ",
            r"\times": " x ",
            r"\Rightarrow": " => ",
            r"\rightarrow": " -> ",
            r"\equiv": " equiv ",
            r"\oint": " contour integral ",
            r"\int": " integral ",
            r"\infty": " infinity ",
        }
        for src, dst in heading_math_aliases.items():
            cleaned = cleaned.replace(src, dst)
        cleaned = cleaned.replace(r"\(", "").replace(r"\)", "")
        cleaned = cleaned.replace(r"\[", "").replace(r"\]", "")
        cleaned = re.sub(r"[{}$^_]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = cleaned.replace('"', "''")
        return NoEscape(escape_latex(cleaned))

    title = g.nodes["book"].get("title", "Book")
    doc.preamble.append(Command("title", _sanitize_heading_text(title)))
    author = str(g.graph.get("author", "") or "").strip()
    if author:
        doc.preamble.append(Command("author", _sanitize_heading_text(author)))
    doc.preamble.append(Command("date", NoEscape(r"\today")))

    # Listings settings (simplified but robust)
    doc.append(
        NoEscape(
            r"\lstset{backgroundcolor=\color[gray]{0.95},basicstyle=\ttfamily\small,breaklines=true,"
            r"breakatwhitespace=false,columns=fullflexible,keepspaces=true,frame=single,"
            r"numbers=left,numberstyle=\tiny,tabsize=4,inputencoding=utf8,extendedchars=true,"
            r"showstringspaces=false}"
        )
    )

    doc.append(NoEscape(r"\hypersetup{pageanchor=false}"))
    doc.append(NoEscape(r"\pagenumbering{roman}"))
    doc.append(NoEscape(r"\maketitle"))
    doc.append(NoEscape(r"\tableofcontents"))
    doc.append(NoEscape(r"\clearpage"))
    doc.append(NoEscape(r"\hypersetup{pageanchor=true}"))
    doc.append(NoEscape(r"\pagenumbering{arabic}"))

    # Add content recursively
    def heading_class_for_depth(d: int):
        if d == 1:
            return Chapter
        if d == 2:
            return Section
        if d == 3:
            return Subsection
        if d == 4:
            return Subsubsection
        if d == 5:
            return Paragraph
        return Subparagraph

    def add_node(parent_container, node_key: str) -> None:
        depth = get_depth(node_key)
        node = g.nodes[node_key]
        title = node.get("title", "")
        summary = node.get("summary", "")
        children = _node_children_sorted(g, node_key)

        Heading = heading_class_for_depth(depth)

        with parent_container.create(Heading(_sanitize_heading_text(title), label=False)):
            if summary and children:
                safe_summary = summary.replace("\\\\", "\\")
                safe_summary, _ = _sanitize_section_tex(safe_summary)
                parent_container.append(NoEscape(safe_summary))

            if children:
                for ch in children:
                    add_node(parent_container, ch)
            else:
                # leaf: append content
                path = node.get("content_file_path")
                if path:
                    p = Path(path)
                    raw = p.read_bytes()
                    tex = _decode_text_bytes(raw)
                    if p.suffix.lower() == ".md":
                        tex = _convert_markdown_to_latex(tex)
                    tex, _ = _sanitize_section_tex(tex)
                    parent_container.append(NoEscape("\n\n"))
                    parent_container.append(NoEscape(tex))
                    parent_container.append(NoEscape("\n\n"))

    for ch in _node_children_sorted(g, "book"):
        add_node(doc, ch)

    tex_filename = safe_filename(title) + ".tex"
    tex_path = out_dir / tex_filename
    doc.generate_tex(filepath=str(tex_path.with_suffix("")))  # pylatex adds .tex
    # Normalize encoding to UTF-8 and sanitize math envs in final document.
    raw = tex_path.read_bytes()
    text = _decode_text_bytes(raw)
    text = _sanitize_final_document_math(text)
    text = _apply_iso690_citations(text, kb, out_dir)
    tex_path.write_text(text, encoding="utf-8")
    return tex_path


def compile_pdf(tex_path: Path) -> Path:
    """
    Compile .tex to .pdf using lualatex if available, fallback to pdflatex.
    """
    import subprocess
    import shutil

    cwd = tex_path.parent
    tex_file = tex_path.name
    pdf_path = tex_path.with_suffix(".pdf")

    # Remove stale aux/toc/out and bib outputs that can break reruns or mask BibTeX failures.
    for suffix in (".aux", ".toc", ".out", ".bbl", ".blg"):
        try:
            tex_path.with_suffix(suffix).unlink()
        except FileNotFoundError:
            pass

    engine = "lualatex"
    if not shutil.which(engine):
        raise RuntimeError("LuaLaTeX not available; please install lualatex to compile PDFs.")
    cmd = [engine, "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", tex_file]

    def _run_latex() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _needs_bibtex() -> bool:
        try:
            tex = tex_path.read_text(encoding="utf-8", errors="ignore")
        except FileNotFoundError:
            return False
        if "\\bibliography" not in tex:
            return False
        return (cwd / "refs.bib").exists()

    def _raise_compile_error(proc: subprocess.CompletedProcess[str] | None, phase: str) -> None:
        log = tex_path.with_suffix(".log")
        msg = ""
        if proc is not None:
            msg = f"{proc.stdout} {proc.stderr}"
        if log.exists():
            tail = " ".join(log.read_text(encoding="utf-8", errors="ignore").splitlines()[-40:])
            msg += "---- LaTeX log tail ----" + tail
        raise RuntimeError(f"Chyba při kompilaci LaTeXu ({phase}).\n{msg}")

    last_proc = _run_latex()
    if last_proc.returncode != 0:
        _raise_compile_error(last_proc, "prvni_pruchod")
    if _needs_bibtex():
        if shutil.which("bibtex"):
            bib_cmd = ["bibtex", tex_path.stem]
            bib_proc = subprocess.run(
                bib_cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if bib_proc.returncode != 0:
                print("[WARN] BibTeX failed; continuing without bibliography.")
        else:
            print("[WARN] BibTeX not available; skipping bibliography.")

    # Run LaTeX twice more for references/TOC.
    last_proc = _run_latex()
    if last_proc.returncode != 0:
        _raise_compile_error(last_proc, "druhy_pruchod")
    last_proc = _run_latex()
    if last_proc.returncode != 0:
        _raise_compile_error(last_proc, "treti_pruchod")
    if pdf_path.exists():
        return pdf_path
    if last_proc.returncode == 0:
        _raise_compile_error(last_proc, "vystup_pdf")
    if last_proc is None or last_proc.returncode != 0:
        # try to print a helpful excerpt
        log = tex_path.with_suffix(".log")
        msg = ""
        if last_proc is not None:
            msg = f"{last_proc.stdout} {last_proc.stderr}"
        if log.exists():
            tail = " ".join(log.read_text(encoding="utf-8", errors="ignore").splitlines()[-40:])
            msg += "---- pdflatex log tail ----" + tail
        raise RuntimeError(f"Chyba při kompilaci LaTeXu.\n{msg}")
    return pdf_path


def _escape_caret_outside_math(text: str) -> str:
    math_envs = {
        "equation",
        "equation*",
        "align",
        "align*",
        "gather",
        "gather*",
        "multline",
        "multline*",
        "eqnarray",
        "eqnarray*",
        "cases",
    }
    out = []
    i = 0
    in_math = False
    in_math_env = None
    while i < len(text):
        if text.startswith("\\begin{", i):
            end = text.find("}", i + 7)
            if end != -1:
                env = text[i + 7 : end]
                if env in math_envs:
                    in_math_env = env
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if text.startswith("\\end{", i):
            end = text.find("}", i + 5)
            if end != -1:
                env = text[i + 5 : end]
                if in_math_env == env:
                    in_math_env = None
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if text.startswith("\\[", i) or text.startswith("\\(", i):
            in_math = True
            out.append(text[i : i + 2])
            i += 2
            continue
        if text.startswith("\\]", i) or text.startswith("\\)", i):
            in_math = False
            out.append(text[i : i + 2])
            i += 2
            continue
        if text.startswith("$$", i):
            in_math = not in_math
            out.append("$$")
            i += 2
            continue
        if text[i] == "$":
            in_math = not in_math
            out.append("$")
            i += 1
            continue
        if text[i] == "^" and not in_math and in_math_env is None:
            out.append("\\textasciicircum{}")
            i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _iso690_footnote_for_kb(source_path: str, loc: str) -> str:
    filename = Path(source_path).name if source_path else "unknown"
    page = extract_page(loc or "")
    if page:
        return f"{filename}. Cit. s. {page}."
    if loc:
        return f"{filename}. Cit. {loc}."
    return f"{filename}."


def _iso690_entry_for_kb(source_path: str) -> str:
    filename = Path(source_path).name if source_path else "unknown"
    title = Path(source_path).stem.replace("_", " ").replace("-", " ").strip() if source_path else ""
    if title:
        return f"{title}. {filename}."
    return f"{filename}."


def _parse_legacy_kb_token(token: str) -> Optional[Tuple[str, str]]:
    token = token.strip()
    if not token:
        return None
    match = re.match(
        r"(?:kb_)?(.+?)_(page|slide|chunk)_([0-9]+)(?:_+chunk_([0-9]+)|_+([0-9]+))?$",
        token,
    )
    if match:
        raw_name, loc_kind, loc_num, _, _ = match.groups()
        loc = f"{loc_kind} {loc_num}"
        return raw_name, loc
    match = re.match(
        r"(?:kb_)?(.+?)_p([0-9]+)(?:_+chunk_([0-9]+)|_+([0-9]+))?$",
        token,
    )
    if match:
        raw_name, loc_num, _, _ = match.groups()
        return raw_name, f"page {loc_num}"
    return None


def _apply_iso690_citations(text: str, kb: Optional[KnowledgeBase], out_dir: Path) -> str:
    cite_key_map: Dict[str, Tuple[str, str]] = {}
    rid_map: Dict[str, Tuple[str, str]] = {}
    if kb is not None:
        for chunk in getattr(kb, "chunks", []):
            source_path = getattr(chunk, "source_path", "")
            loc = getattr(chunk, "loc", "")
            rid = getattr(chunk, "rid", "")
            cite_key = kb_cite_key(source_path, loc)
            cite_key_map[cite_key] = (source_path, loc)
            if rid:
                rid_map[str(rid)] = (source_path, loc)
    else:
        index_path = out_dir / "kb_sources.json"
        if index_path.exists():
            try:
                data = json.loads(index_path.read_text(encoding="utf-8"))
                for key, entry in (data.get("cite_keys") or {}).items():
                    source_path = str(entry.get("source_path", ""))
                    loc = str(entry.get("loc", ""))
                    if source_path:
                        cite_key_map[key] = (source_path, loc)
                for rid, entry in (data.get("rids") or {}).items():
                    source_path = str(entry.get("source_path", ""))
                    loc = str(entry.get("loc", ""))
                    if source_path:
                        rid_map[str(rid)] = (source_path, loc)
            except Exception:
                return text

    def _sanitize_kb_key(value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.lower())
        return cleaned.strip("_") or "unknown"

    source_index: Dict[str, str] = {}
    source_paths = {info[0] for info in cite_key_map.values()} | {info[0] for info in rid_map.values()}
    for source_path in sorted(p for p in source_paths if p):
        path = Path(source_path)
        stem_key = _sanitize_kb_key(path.stem)
        source_index.setdefault(stem_key, source_path)
        name_key = _sanitize_kb_key(path.name)
        source_index.setdefault(name_key, source_path)
        ext = path.suffix.lstrip(".")
        if ext:
            stem_ext_key = _sanitize_kb_key(f"{path.stem}_{ext}")
            source_index.setdefault(stem_ext_key, source_path)

    def _normalize_source_token(token: str) -> str:
        return token.strip().strip(".,;")

    def _resolve_source_path(raw_name: str) -> str:
        key = _sanitize_kb_key(raw_name)
        candidate = source_index.get(key)
        if candidate:
            return candidate
        for ext in ("pdf", "docx", "pptx", "md", "txt"):
            suffix = f"_{ext}"
            if key.endswith(suffix):
                base_key = key[: -len(suffix)]
                candidate = source_index.get(base_key)
                if candidate:
                    return candidate
                raw_base = raw_name[: -len(suffix)]
                return f"{raw_base}.{ext}"
        return raw_name

    def _resolve_token(token: str) -> Optional[Tuple[str, str]]:
        token = _normalize_source_token(token)
        if not token:
            return None
        if token in cite_key_map:
            return cite_key_map[token]
        if token in rid_map:
            return rid_map[token]
        legacy = _parse_legacy_kb_token(token)
        if legacy:
            raw_name, loc = legacy
            return _resolve_source_path(raw_name), loc
        return None

    used_sources: Dict[str, None] = {}
    used_cite_keys, used_rids = extract_citations(text)
    for key in used_cite_keys:
        info = _resolve_token(key)
        if info:
            used_sources[info[0]] = None
    for rid in used_rids:
        info = _resolve_token(rid)
        if info:
            used_sources[info[0]] = None
    for token in re.findall(r"Source:\s*([^\s}]+)", text):
        info = _resolve_token(token)
        if info:
            used_sources[info[0]] = None

    def _format_token(token: str) -> Optional[str]:
        info = _resolve_token(token)
        if not info:
            return None
        source_path, loc = info
        used_sources[source_path] = None
        return _iso690_footnote_for_kb(source_path, loc)

    def _replace_cite(match: re.Match[str]) -> str:
        keys = match.group(1)
        kb_entries = []
        non_kb_keys = []
        for raw_key in keys.split(","):
            key = raw_key.strip()
            if not key:
                continue
            formatted = _format_token(key)
            if formatted:
                kb_entries.append(formatted)
            else:
                non_kb_keys.append(key)
        if not kb_entries:
            return match.group(0)
        joined = " ".join(kb_entries)
        footnote = f"\\footnote{{{joined}}}"
        if non_kb_keys:
            return f"\\cite{{{', '.join(non_kb_keys)}}}{footnote}"
        return footnote

    def _replace_source_footnote(match: re.Match[str]) -> str:
        body = match.group(1)
        tokens = re.findall(r"Source:\\s*([^\\s}]+)", body)
        if not tokens:
            return match.group(0)
        entries = []
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            entry = _format_token(token)
            if entry:
                entries.append(entry)
        if not entries:
            return match.group(0)
        return f"\\footnote{{{' '.join(entries)}}}"

    text = re.sub(r"\\cite[a-zA-Z*]*\{([^}]+)\}", _replace_cite, text)
    text = re.sub(r"\\footnote\{(.*?)\}", _replace_source_footnote, text, flags=re.S)

    has_refs = re.search(
        r"\\chapter\\*\\{(?:\\\\bibname|[^}]*literatura[^}]*)\\}",
        text,
        flags=re.IGNORECASE,
    )
    if used_sources and not has_refs:
        entries = sorted(used_sources.keys())
        items = "\n".join(f"\\item {_iso690_entry_for_kb(path)}" for path in entries)
        literature = (
            "\n\\chapter*{\\bibname}\n"
            "\\addcontentsline{toc}{chapter}{\\bibname}\n"
            "\\begin{enumerate}\n"
            f"{items}\n"
            "\\end{enumerate}\n"
        )
        text = text.replace("\\end{document}", literature + "\n\\end{document}")

    return text


def _apply_iso690_citations_v2(text: str, kb: Optional[KnowledgeBase], out_dir: Path) -> str:
    def _excerpt_from_text(raw: str, max_words: int = 12, max_chars: int = 120) -> str:
        cleaned = re.sub(r"\s+", " ", (raw or "").strip())
        if not cleaned:
            return ""
        words = cleaned.split()
        excerpt = " ".join(words[:max_words])
        if len(excerpt) > max_chars:
            excerpt = excerpt[:max_chars].rstrip()
        return excerpt

    def _extract_chunk_index(loc: str) -> Optional[str]:
        match = re.search(r"chunk\s+(\d+)", loc or "", flags=re.IGNORECASE)
        return match.group(1) if match else None

    def _sanitize_kb_key(value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.lower())
        return cleaned.strip("_") or "unknown"

    def _parse_token_hint(token: str) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
        token = token.strip()
        if not token:
            return None
        match = re.match(
            r"(?:kb_)?(.+?)_(page|slide|chunk)_([0-9]+)(?:_+chunk_([0-9]+)|_+([0-9]+))?$",
            token,
        )
        if match:
            raw_name, loc_kind, loc_num, chunk_a, chunk_b = match.groups()
            chunk_num = chunk_a or chunk_b
            if loc_kind == "chunk":
                return raw_name, None, loc_num
            return raw_name, loc_num, chunk_num
        match = re.match(r"kb_(.+?)_([0-9]+)_([0-9]+)$", token)
        if match:
            raw_name, loc_num, chunk_num = match.groups()
            return raw_name, loc_num, chunk_num
        match = re.match(r"kb_(.+?)_([0-9]+)$", token)
        if match:
            raw_name, loc_num = match.groups()
            return raw_name, loc_num, None
        match = re.match(
            r"(?:kb_)?(.+?)_p([0-9]+)(?:_+chunk_([0-9]+)|_+([0-9]+))?$",
            token,
        )
        if match:
            raw_name, loc_num, chunk_a, chunk_b = match.groups()
            chunk_num = chunk_a or chunk_b
            return raw_name, loc_num, chunk_num
        return None

    entries: List[Dict[str, str]] = []
    by_rid: Dict[str, Dict[str, str]] = {}
    by_cite_key: Dict[str, Dict[str, str]] = {}
    page_keys: Dict[str, List[Dict[str, str]]] = {}

    if kb is not None:
        for chunk in getattr(kb, "chunks", []):
            source_path = str(getattr(chunk, "source_path", "") or "")
            loc = str(getattr(chunk, "loc", "") or "")
            rid = str(getattr(chunk, "rid", "") or "")
            cite_key = str(getattr(chunk, "cite_key", "") or "")
            excerpt = _excerpt_from_text(getattr(chunk, "text", "") or "")
            entry = {
                "source_path": source_path,
                "loc": loc,
                "rid": rid,
                "cite_key": cite_key,
                "excerpt": excerpt,
                "page": extract_page(loc or "") or "",
                "chunk": _extract_chunk_index(loc or "") or "",
            }
            entries.append(entry)
            if rid:
                by_rid[rid] = entry
            if cite_key:
                by_cite_key[cite_key] = entry
            page_key = kb_cite_key(source_path, loc)
            page_keys.setdefault(page_key, []).append(entry)
    else:
        index_path = out_dir / "kb_sources.json"
        if index_path.exists():
            try:
                data = json.loads(index_path.read_text(encoding="utf-8"))
                for entry in data.get("chunks") or []:
                    source_path = str(entry.get("source_path", "") or "")
                    loc = str(entry.get("loc", "") or "")
                    rid = str(entry.get("rid", "") or "")
                    cite_key = str(entry.get("cite_key", "") or "")
                    excerpt = _excerpt_from_text(str(entry.get("excerpt", "") or ""))
                    item = {
                        "source_path": source_path,
                        "loc": loc,
                        "rid": rid,
                        "cite_key": cite_key,
                        "excerpt": excerpt,
                        "page": extract_page(loc or "") or "",
                        "chunk": _extract_chunk_index(loc or "") or "",
                    }
                    entries.append(item)
                    if rid:
                        by_rid[rid] = item
                    if cite_key:
                        by_cite_key[cite_key] = item
                    page_key = kb_cite_key(source_path, loc)
                    page_keys.setdefault(page_key, []).append(item)
                if not entries:
                    for key, entry in (data.get("cite_keys") or {}).items():
                        source_path = str(entry.get("source_path", "") or "")
                        loc = str(entry.get("loc", "") or "")
                        excerpt = _excerpt_from_text(str(entry.get("excerpt", "") or ""))
                        item = {
                            "source_path": source_path,
                            "loc": loc,
                            "rid": "",
                            "cite_key": key,
                            "excerpt": excerpt,
                            "page": extract_page(loc or "") or "",
                            "chunk": _extract_chunk_index(loc or "") or "",
                        }
                        entries.append(item)
                        if key:
                            by_cite_key[key] = item
                        page_keys.setdefault(key, []).append(item)
                    for rid, entry in (data.get("rids") or {}).items():
                        source_path = str(entry.get("source_path", "") or "")
                        loc = str(entry.get("loc", "") or "")
                        excerpt = _excerpt_from_text(str(entry.get("excerpt", "") or ""))
                        item = {
                            "source_path": source_path,
                            "loc": loc,
                            "rid": str(rid),
                            "cite_key": "",
                            "excerpt": excerpt,
                            "page": extract_page(loc or "") or "",
                            "chunk": _extract_chunk_index(loc or "") or "",
                        }
                        entries.append(item)
                        by_rid[str(rid)] = item
            except Exception:
                return text

    source_index: Dict[str, str] = {}
    for source_path in sorted({e.get("source_path", "") for e in entries}):
        if not source_path:
            continue
        path = Path(source_path)
        source_index.setdefault(_sanitize_kb_key(path.stem), source_path)
        source_index.setdefault(_sanitize_kb_key(path.name), source_path)
        ext = path.suffix.lstrip(".")
        if ext:
            source_index.setdefault(_sanitize_kb_key(f"{path.stem}_{ext}"), source_path)

    by_source_page_chunk: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    by_source_page: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
    for entry in entries:
        source_path = entry.get("source_path", "")
        page = entry.get("page", "")
        chunk = entry.get("chunk", "")
        if source_path and page and chunk:
            by_source_page_chunk[(source_path, page, chunk)] = entry
        if source_path and page:
            by_source_page.setdefault((source_path, page), []).append(entry)

    def _cite_key_root(key: str) -> str:
        return re.sub(r"_(?:page|slide|chunk)_[0-9]+(?:_[0-9]+)?(?:_chunk_[0-9]+)?$", "", key)

    by_cite_root: Dict[str, Dict[str, str]] = {}
    for entry in entries:
        cite_key = entry.get("cite_key", "")
        if not cite_key:
            continue
        by_cite_root.setdefault(_cite_key_root(cite_key), entry)

    def _cite_key_root(key: str) -> str:
        return re.sub(r"_(?:page|slide|chunk)_[0-9]+(?:_[0-9]+)?(?:_chunk_[0-9]+)?$", "", key)

    by_cite_root: Dict[str, Dict[str, str]] = {}
    for entry in entries:
        cite_key = entry.get("cite_key", "")
        if not cite_key:
            continue
        by_cite_root.setdefault(_cite_key_root(cite_key), entry)

    def _resolve_source_path(raw_name: str) -> str:
        key = _sanitize_kb_key(raw_name)
        candidate = source_index.get(key)
        if candidate:
            return candidate
        for ext in ("pdf", "docx", "pptx", "md", "txt"):
            suffix = f"_{ext}"
            if key.endswith(suffix):
                base_key = key[: -len(suffix)]
                candidate = source_index.get(base_key)
                if candidate:
                    return candidate
                raw_base = raw_name[: -len(suffix)]
                return f"{raw_base}.{ext}"
        return raw_name

    def _resolve_entry(token: str) -> Optional[Dict[str, str]]:
        token = token.strip().strip(".,;")
        if not token:
            return None
        normalized = _normalize_citation_token(token)
        if normalized != token:
            token = normalized
        if token in by_rid:
            return by_rid[token]
        if token in by_cite_key:
            return by_cite_key[token]
        if token in by_cite_root:
            return by_cite_root[token]
        if token in page_keys:
            candidates = page_keys[token]
            return candidates[0] if candidates else None
        hint = _parse_token_hint(token)
        if hint:
            raw_name, page_num, chunk_num = hint
            source_path = _resolve_source_path(raw_name)
            if source_path and page_num and chunk_num:
                entry = by_source_page_chunk.get((source_path, page_num, chunk_num))
                if entry:
                    return entry
            if source_path and page_num:
                candidates = by_source_page.get((source_path, page_num), [])
                if len(candidates) == 1:
                    return candidates[0]
                if candidates:
                    return candidates[0]
        return None

    citation_numbers: Dict[str, int] = {}
    ordered_entries: List[Dict[str, str]] = []

    def _entry_key(entry: Dict[str, str]) -> str:
        return f"{entry.get('source_path','')}|{entry.get('loc','')}"

    def _get_number(entry: Dict[str, str]) -> int:
        key = _entry_key(entry)
        number = citation_numbers.get(key)
        if number is None:
            number = len(citation_numbers) + 1
            citation_numbers[key] = number
            ordered_entries.append(entry)
        return number

    def _format_citation_numbers(nums: List[int]) -> str:
        if not nums:
            return ""
        uniq = []
        for n in nums:
            if n not in uniq:
                uniq.append(n)
        joined = ",".join(str(n) for n in uniq)
        return f"[{joined}]"

    def _replace_cite(match: re.Match[str]) -> str:
        keys = match.group(1)
        nums: List[int] = []
        for raw_key in keys.split(","):
            key = raw_key.strip()
            if not key:
                continue
            entry = _resolve_entry(key)
            if entry:
                nums.append(_get_number(entry))
        if not nums:
            return match.group(0)
        return _format_citation_numbers(nums)

    def _replace_footnote(match: re.Match[str]) -> str:
        body = match.group(1)
        tokens = re.findall(r"Source:\\s*([^\\s}]+)", body)
        nums: List[int] = []
        for token in tokens:
            entry = _resolve_entry(token)
            if entry:
                nums.append(_get_number(entry))
        if nums:
            return _format_citation_numbers(nums)
        cleaned = re.sub(r"\\s+", " ", body.strip())
        if cleaned:
            return f" ({cleaned})"
        return ""

    text = re.sub(r"\\cite[a-zA-Z*]*\{([^}]+)\}", _replace_cite, text)
    text = re.sub(r"\\footnote\{(.*?)\}", _replace_footnote, text, flags=re.S)

    has_refs = re.search(
        r"\\chapter\\*\\{(?:\\\\bibname|[^}]*literatura[^}]*)\\}",
        text,
        flags=re.IGNORECASE,
    )
    if ordered_entries and not has_refs:
        items = []
        for entry in ordered_entries:
            num = citation_numbers.get(_entry_key(entry), 0)
            source_path = entry.get("source_path", "") or "unknown"
            filename = escape_latex(Path(source_path).name)
            page = entry.get("page", "")
            chunk = entry.get("chunk", "")
            excerpt = escape_latex(entry.get("excerpt", ""))
            parts = [f"\"{filename}\""]
            if page:
                parts.append(f"str. {page}")
            if chunk:
                parts.append(f"chunk {chunk}")
            if excerpt:
                parts.append(f"\"{excerpt}\"")
            line = ", ".join(parts) + "."
            items.append(f"\\item[\\textbf{{[{num}]}}] {line}")
        literature = (
            "\n\\chapter*{\\bibname}\n"
            "\\addcontentsline{toc}{chapter}{\\bibname}\n"
            "\\begin{enumerate}\n"
            + "\n".join(items)
            + "\n\\end{enumerate}\n"
        )
        text = text.replace("\\end{document}", literature + "\n\\end{document}")

    return text


_apply_iso690_citations = _apply_iso690_citations_v2


def _apply_iso690_citations_v3(text: str, kb: Optional[KnowledgeBase], out_dir: Path) -> str:
    def _excerpt_from_text(raw: str, max_words: int = 12, max_chars: int = 120) -> str:
        cleaned = re.sub(r"\s+", " ", (raw or "").strip())
        if not cleaned:
            return ""
        words = cleaned.split()
        excerpt = " ".join(words[:max_words])
        if len(excerpt) > max_chars:
            excerpt = excerpt[:max_chars].rstrip()
        return excerpt

    def _extract_chunk_index(loc: str) -> Optional[str]:
        match = re.search(r"chunk\s+(\d+)", loc or "", flags=re.IGNORECASE)
        return match.group(1) if match else None

    def _sanitize_kb_key(value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.lower())
        return cleaned.strip("_") or "unknown"

    def _parse_token_hint(token: str) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
        token = token.strip()
        if not token:
            return None
        match = re.match(
            r"(?:kb_)?(.+?)_(page|slide|chunk)_([0-9]+)(?:_+chunk_([0-9]+)|_+([0-9]+))?$",
            token,
        )
        if match:
            raw_name, loc_kind, loc_num, chunk_a, chunk_b = match.groups()
            chunk_num = chunk_a or chunk_b
            if loc_kind == "chunk":
                return raw_name, None, loc_num
            return raw_name, loc_num, chunk_num
        match = re.match(
            r"(?:kb_)?(.+?)_p([0-9]+)(?:_+chunk_([0-9]+)|_+([0-9]+))?$",
            token,
        )
        if match:
            raw_name, loc_num, chunk_a, chunk_b = match.groups()
            chunk_num = chunk_a or chunk_b
            return raw_name, loc_num, chunk_num
        return None

    entries: List[Dict[str, str]] = []
    by_rid: Dict[str, Dict[str, str]] = {}
    by_cite_key: Dict[str, Dict[str, str]] = {}
    page_keys: Dict[str, List[Dict[str, str]]] = {}

    if kb is not None:
        for chunk in getattr(kb, "chunks", []):
            source_path = str(getattr(chunk, "source_path", "") or "")
            loc = str(getattr(chunk, "loc", "") or "")
            rid = str(getattr(chunk, "rid", "") or "")
            cite_key = str(getattr(chunk, "cite_key", "") or "")
            excerpt = _excerpt_from_text(getattr(chunk, "text", "") or "")
            entry = {
                "source_path": source_path,
                "loc": loc,
                "rid": rid,
                "cite_key": cite_key,
                "excerpt": excerpt,
                "page": extract_page(loc or "") or "",
                "chunk": _extract_chunk_index(loc or "") or "",
            }
            entries.append(entry)
            if rid:
                by_rid[rid] = entry
            if cite_key:
                by_cite_key[cite_key] = entry
            page_key = kb_cite_key(source_path, loc)
            page_keys.setdefault(page_key, []).append(entry)
    else:
        index_path = out_dir / "kb_sources.json"
        if index_path.exists():
            try:
                data = json.loads(index_path.read_text(encoding="utf-8"))
                for entry in data.get("chunks") or []:
                    source_path = str(entry.get("source_path", "") or "")
                    loc = str(entry.get("loc", "") or "")
                    rid = str(entry.get("rid", "") or "")
                    cite_key = str(entry.get("cite_key", "") or "")
                    excerpt = _excerpt_from_text(str(entry.get("excerpt", "") or ""))
                    item = {
                        "source_path": source_path,
                        "loc": loc,
                        "rid": rid,
                        "cite_key": cite_key,
                        "excerpt": excerpt,
                        "page": extract_page(loc or "") or "",
                        "chunk": _extract_chunk_index(loc or "") or "",
                    }
                    entries.append(item)
                    if rid:
                        by_rid[rid] = item
                    if cite_key:
                        by_cite_key[cite_key] = item
                    page_key = kb_cite_key(source_path, loc)
                    page_keys.setdefault(page_key, []).append(item)
                if not entries:
                    for key, entry in (data.get("cite_keys") or {}).items():
                        source_path = str(entry.get("source_path", "") or "")
                        loc = str(entry.get("loc", "") or "")
                        excerpt = _excerpt_from_text(str(entry.get("excerpt", "") or ""))
                        item = {
                            "source_path": source_path,
                            "loc": loc,
                            "rid": "",
                            "cite_key": key,
                            "excerpt": excerpt,
                            "page": extract_page(loc or "") or "",
                            "chunk": _extract_chunk_index(loc or "") or "",
                        }
                        entries.append(item)
                        if key:
                            by_cite_key[key] = item
                        page_keys.setdefault(key, []).append(item)
                    for rid, entry in (data.get("rids") or {}).items():
                        source_path = str(entry.get("source_path", "") or "")
                        loc = str(entry.get("loc", "") or "")
                        excerpt = _excerpt_from_text(str(entry.get("excerpt", "") or ""))
                        item = {
                            "source_path": source_path,
                            "loc": loc,
                            "rid": str(rid),
                            "cite_key": "",
                            "excerpt": excerpt,
                            "page": extract_page(loc or "") or "",
                            "chunk": _extract_chunk_index(loc or "") or "",
                        }
                        entries.append(item)
                        by_rid[str(rid)] = item
            except Exception:
                return text

    source_index: Dict[str, str] = {}
    for source_path in sorted({e.get("source_path", "") for e in entries}):
        if not source_path:
            continue
        path = Path(source_path)
        source_index.setdefault(_sanitize_kb_key(path.stem), source_path)
        source_index.setdefault(_sanitize_kb_key(path.name), source_path)
        ext = path.suffix.lstrip(".")
        if ext:
            source_index.setdefault(_sanitize_kb_key(f"{path.stem}_{ext}"), source_path)

    by_source_page_chunk: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    by_source_page: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
    for entry in entries:
        source_path = entry.get("source_path", "")
        page = entry.get("page", "")
        chunk = entry.get("chunk", "")
        if source_path and page and chunk:
            by_source_page_chunk[(source_path, page, chunk)] = entry
        if source_path and page:
            by_source_page.setdefault((source_path, page), []).append(entry)

    def _cite_key_root(key: str) -> str:
        return re.sub(r"_(?:page|slide|chunk)_[0-9]+(?:_[0-9]+)?(?:_chunk_[0-9]+)?$", "", key)

    by_cite_root: Dict[str, Dict[str, str]] = {}
    for entry in entries:
        cite_key = entry.get("cite_key", "")
        if not cite_key:
            continue
        by_cite_root.setdefault(_cite_key_root(cite_key), entry)

    def _resolve_source_path(raw_name: str) -> str:
        key = _sanitize_kb_key(raw_name)
        candidate = source_index.get(key)
        if candidate:
            return candidate
        for ext in ("pdf", "docx", "pptx", "md", "txt"):
            suffix = f"_{ext}"
            if key.endswith(suffix):
                base_key = key[: -len(suffix)]
                candidate = source_index.get(base_key)
                if candidate:
                    return candidate
                raw_base = raw_name[: -len(suffix)]
                return f"{raw_base}.{ext}"
        return raw_name

    def _normalize_citation_token(token: str) -> str:
        token = token.strip()
        token = re.sub(r"_(page|slide|chunk)_([0-9]+):([0-9]+)", r"_\1_\2_\3", token)
        token = token.replace(
            "kb_pr_11_metoda_singularit_pptx_396ad930_",
            "kb_11_metoda_singularit_pptx_396ad930_",
        )
        match = re.search(
            r"pr-08 .*metoda singularit.*profil.*docx_chunk_(\d+)",
            token,
            flags=re.IGNORECASE,
        )
        if match:
            return f"kb_pr_08_metoda_singularit_pro_tenk_profily_docx_4425a50f_chunk_{match.group(1)}"
        return token

    def _resolve_entry(token: str) -> Optional[Dict[str, str]]:
        token = _normalize_citation_token(token.strip().strip(".,;"))
        if not token:
            return None
        if token in by_rid:
            return by_rid[token]
        if token in by_cite_key:
            return by_cite_key[token]
        if token in by_cite_root:
            return by_cite_root[token]
        if token in page_keys:
            candidates = page_keys[token]
            return candidates[0] if candidates else None
        hint = _parse_token_hint(token)
        if hint:
            raw_name, page_num, chunk_num = hint
            source_path = _resolve_source_path(raw_name)
            if source_path and page_num and chunk_num:
                entry = by_source_page_chunk.get((source_path, page_num, chunk_num))
                if entry:
                    return entry
            if source_path and page_num:
                candidates = by_source_page.get((source_path, page_num), [])
                if len(candidates) == 1:
                    return candidates[0]
                if candidates:
                    return candidates[0]
        return None

    citation_numbers: Dict[str, int] = {}
    ordered_entries: List[Dict[str, str]] = []

    def _entry_key(entry: Dict[str, str]) -> str:
        return f"{entry.get('source_path','')}|{entry.get('loc','')}"

    def _get_number(entry: Dict[str, str]) -> int:
        key = _entry_key(entry)
        number = citation_numbers.get(key)
        if number is None:
            number = len(citation_numbers) + 1
            citation_numbers[key] = number
            ordered_entries.append(entry)
        return number

    def _format_citation_numbers(nums: List[int]) -> str:
        if not nums:
            return ""
        uniq = []
        for n in nums:
            if n not in uniq:
                uniq.append(n)
        joined = ",".join(str(n) for n in uniq)
        return f"[{joined}]"

    def _replace_cite(match: re.Match[str]) -> str:
        keys = match.group(1)
        nums: List[int] = []
        for raw_key in keys.split(","):
            key = raw_key.strip()
            if not key:
                continue
            entry = _resolve_entry(key)
            if entry:
                nums.append(_get_number(entry))
        if not nums:
            return match.group(0)
        return _format_citation_numbers(nums)

    def _replace_footnote(match: re.Match[str]) -> str:
        body = match.group(1)
        tokens = re.findall(r"Source:\s*([^\s}]+)", body)
        nums: List[int] = []
        for token in tokens:
            entry = _resolve_entry(token)
            if entry:
                nums.append(_get_number(entry))
        if nums:
            return _format_citation_numbers(nums)
        cleaned = re.sub(r"\s+", " ", body.strip())
        if cleaned:
            return f" ({cleaned})"
        return ""

    text = re.sub(r"\\cite[a-zA-Z*]*\{([^}]+)\}", _replace_cite, text)
    text = re.sub(r"\\footnote\{(.*?)\}", _replace_footnote, text, flags=re.S)

    has_refs = re.search(
        r"\\chapter\\*\\{(?:\\\\bibname|[^}]*literatura[^}]*)\\}",
        text,
        flags=re.IGNORECASE,
    )
    if ordered_entries and not has_refs:
        items = []
        for entry in ordered_entries:
            num = citation_numbers.get(_entry_key(entry), 0)
            source_path = entry.get("source_path", "") or "unknown"
            filename = Path(source_path).name
            page = entry.get("page", "")
            chunk = entry.get("chunk", "")
            excerpt = entry.get("excerpt", "")
            parts = [f"\"{filename}\""]
            if page:
                parts.append(f"str. {page}")
            if chunk:
                parts.append(f"chunk {chunk}")
            if excerpt:
                parts.append(f"\"{excerpt}\"")
            line = ", ".join(parts) + "."
            items.append(f"\\item[\\textbf{{[{num}]}}] {line}")
        literature_title = "\\bibname"
        literature = (
            f"\n\\chapter*{{{literature_title}}}\n"
            f"\\addcontentsline{{toc}}{{chapter}}{{{literature_title}}}\n"
            "\\begin{enumerate}\n"
            + "\n".join(items)
            + "\n\\end{enumerate}\n"
        )
        text = text.replace("\\end{document}", literature + "\n\\end{document}")

    return text


_apply_iso690_citations = _apply_iso690_citations_v3


def _apply_bibtex_citations(text: str, kb: Optional[KnowledgeBase], out_dir: Path) -> str:
    def _excerpt_from_text(raw: str, max_words: int = 12, max_chars: int = 120) -> str:
        cleaned = re.sub(r"\s+", " ", (raw or "").strip())
        if not cleaned:
            return ""
        words = cleaned.split()
        excerpt = " ".join(words[:max_words])
        if len(excerpt) > max_chars:
            excerpt = excerpt[:max_chars].rstrip()
        return excerpt

    def _extract_chunk_index(loc: str) -> Optional[str]:
        match = re.search(r"chunk\s+(\d+)", loc or "", flags=re.IGNORECASE)
        return match.group(1) if match else None

    def _sanitize_key(value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.lower())
        return cleaned.strip("_") or "ref"

    def _parse_token_hint(token: str) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
        token = token.strip()
        if not token:
            return None
        match = re.match(
            r"(?:kb_)?(.+?)_(page|slide|chunk)_([0-9]+)(?:_+chunk_([0-9]+)|_+([0-9]+))?$",
            token,
        )
        if match:
            raw_name, loc_kind, loc_num, chunk_a, chunk_b = match.groups()
            chunk_num = chunk_a or chunk_b
            if loc_kind == "chunk":
                return raw_name, None, loc_num
            return raw_name, loc_num, chunk_num
        match = re.match(
            r"(?:kb_)?(.+?)_p([0-9]+)(?:_+chunk_([0-9]+)|_+([0-9]+))?$",
            token,
        )
        if match:
            raw_name, loc_num, chunk_a, chunk_b = match.groups()
            chunk_num = chunk_a or chunk_b
            return raw_name, loc_num, chunk_num
        return None

    entries: List[Dict[str, str]] = []
    by_rid: Dict[str, Dict[str, str]] = {}
    by_cite_key: Dict[str, Dict[str, str]] = {}
    page_keys: Dict[str, List[Dict[str, str]]] = {}

    if kb is not None:
        for chunk in getattr(kb, "chunks", []):
            source_path = str(getattr(chunk, "source_path", "") or "")
            loc = str(getattr(chunk, "loc", "") or "")
            rid = str(getattr(chunk, "rid", "") or "")
            cite_key = str(getattr(chunk, "cite_key", "") or "")
            excerpt = _excerpt_from_text(getattr(chunk, "text", "") or "")
            entry = {
                "source_path": source_path,
                "loc": loc,
                "rid": rid,
                "cite_key": cite_key,
                "excerpt": excerpt,
                "page": extract_page(loc or "") or "",
                "chunk": _extract_chunk_index(loc or "") or "",
            }
            entries.append(entry)
            if rid:
                by_rid[rid] = entry
            if cite_key:
                by_cite_key[cite_key] = entry
            page_key = kb_cite_key(source_path, loc)
            page_keys.setdefault(page_key, []).append(entry)
    else:
        index_path = out_dir / "kb_sources.json"
        if index_path.exists():
            try:
                data = json.loads(index_path.read_text(encoding="utf-8"))
                for entry in data.get("chunks") or []:
                    source_path = str(entry.get("source_path", "") or "")
                    loc = str(entry.get("loc", "") or "")
                    rid = str(entry.get("rid", "") or "")
                    cite_key = str(entry.get("cite_key", "") or "")
                    excerpt = _excerpt_from_text(str(entry.get("excerpt", "") or ""))
                    item = {
                        "source_path": source_path,
                        "loc": loc,
                        "rid": rid,
                        "cite_key": cite_key,
                        "excerpt": excerpt,
                        "page": extract_page(loc or "") or "",
                        "chunk": _extract_chunk_index(loc or "") or "",
                    }
                    entries.append(item)
                    if rid:
                        by_rid[rid] = item
                    if cite_key:
                        by_cite_key[cite_key] = item
                    page_key = kb_cite_key(source_path, loc)
                    page_keys.setdefault(page_key, []).append(item)
                if not entries:
                    for key, entry in (data.get("cite_keys") or {}).items():
                        source_path = str(entry.get("source_path", "") or "")
                        loc = str(entry.get("loc", "") or "")
                        excerpt = _excerpt_from_text(str(entry.get("excerpt", "") or ""))
                        item = {
                            "source_path": source_path,
                            "loc": loc,
                            "rid": "",
                            "cite_key": key,
                            "excerpt": excerpt,
                            "page": extract_page(loc or "") or "",
                            "chunk": _extract_chunk_index(loc or "") or "",
                        }
                        entries.append(item)
                        if key:
                            by_cite_key[key] = item
                        page_keys.setdefault(key, []).append(item)
                    for rid, entry in (data.get("rids") or {}).items():
                        source_path = str(entry.get("source_path", "") or "")
                        loc = str(entry.get("loc", "") or "")
                        excerpt = _excerpt_from_text(str(entry.get("excerpt", "") or ""))
                        item = {
                            "source_path": source_path,
                            "loc": loc,
                            "rid": str(rid),
                            "cite_key": "",
                            "excerpt": excerpt,
                            "page": extract_page(loc or "") or "",
                            "chunk": _extract_chunk_index(loc or "") or "",
                        }
                        entries.append(item)
                        by_rid[str(rid)] = item
            except Exception:
                return text

    source_index: Dict[str, str] = {}
    for source_path in sorted({e.get("source_path", "") for e in entries}):
        if not source_path:
            continue
        path = Path(source_path)
        source_index.setdefault(_sanitize_key(path.stem), source_path)
        source_index.setdefault(_sanitize_key(path.name), source_path)
        ext = path.suffix.lstrip(".")
        if ext:
            source_index.setdefault(_sanitize_key(f"{path.stem}_{ext}"), source_path)

    by_source_page_chunk: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    by_source_page: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
    for entry in entries:
        source_path = entry.get("source_path", "")
        page = entry.get("page", "")
        chunk = entry.get("chunk", "")
        if source_path and page and chunk:
            by_source_page_chunk[(source_path, page, chunk)] = entry
        if source_path and page:
            by_source_page.setdefault((source_path, page), []).append(entry)

    def _cite_key_root(key: str) -> str:
        return re.sub(r"_(?:page|slide|chunk)_[0-9]+(?:_[0-9]+)?(?:_chunk_[0-9]+)?$", "", key)

    by_cite_root: Dict[str, Dict[str, str]] = {}
    for entry in entries:
        cite_key = entry.get("cite_key", "")
        if not cite_key:
            continue
        by_cite_root.setdefault(_cite_key_root(cite_key), entry)

    def _resolve_source_path(raw_name: str) -> str:
        key = _sanitize_key(raw_name)
        candidate = source_index.get(key)
        if candidate:
            return candidate
        for ext in ("pdf", "docx", "pptx", "md", "txt"):
            suffix = f"_{ext}"
            if key.endswith(suffix):
                base_key = key[: -len(suffix)]
                candidate = source_index.get(base_key)
                if candidate:
                    return candidate
                raw_base = raw_name[: -len(suffix)]
                return f"{raw_base}.{ext}"
        return raw_name

    def _normalize_citation_token(token: str) -> str:
        token = token.strip()
        token = re.sub(r"_(page|slide|chunk)_([0-9]+):([0-9]+)", r"_\1_\2_\3", token)
        token = token.replace(
            "kb_pr_11_metoda_singularit_pptx_396ad930_",
            "kb_11_metoda_singularit_pptx_396ad930_",
        )
        token = re.sub(
            r"pr-08 _Metoda singularit pro tenké profily\.docx_chunk_(\d+)",
            r"kb_pr_08_metoda_singularit_pro_tenk_profily_docx_4425a50f_chunk_\1",
            token,
        )
        return token

    def _ensure_cite_key(entry: Dict[str, str]) -> str:
        key = entry.get("cite_key", "")
        if key:
            return key
        source_path = entry.get("source_path", "") or "source"
        stem = Path(source_path).stem
        page = entry.get("page", "")
        chunk = entry.get("chunk", "")
        raw = f"kb_{stem}"
        if page:
            raw += f"_p{page}"
        if chunk:
            raw += f"_c{chunk}"
        key = _sanitize_key(raw)
        suffix = 1
        base = key
        while key in by_cite_key and by_cite_key[key] is not entry:
            suffix += 1
            key = f"{base}_{suffix}"
        entry["cite_key"] = key
        by_cite_key[key] = entry
        return key

    def _resolve_entry(token: str) -> Optional[Dict[str, str]]:
        token = token.strip().strip(".,;")
        if not token:
            return None
        if token in by_rid:
            return by_rid[token]
        if token in by_cite_key:
            return by_cite_key[token]
        if token in page_keys:
            candidates = page_keys[token]
            return candidates[0] if candidates else None
        hint = _parse_token_hint(token)
        if hint:
            raw_name, page_num, chunk_num = hint
            source_path = _resolve_source_path(raw_name)
            if source_path and page_num and chunk_num:
                entry = by_source_page_chunk.get((source_path, page_num, chunk_num))
                if entry:
                    return entry
            if source_path and page_num:
                candidates = by_source_page.get((source_path, page_num), [])
                if candidates:
                    return candidates[0]
        return None

    used_keys: List[str] = []
    used_set: set[str] = set()

    def _mark_used(entry: Dict[str, str]) -> str:
        key = _ensure_cite_key(entry)
        if key not in used_set:
            used_set.add(key)
            used_keys.append(key)
        return key

    cite_pattern = re.compile(
        r"(?P<cmd>\\cite[a-zA-Z*]*)"
        r"(?P<opts>(?:\[[^\]]*\]\s*)*)"
        r"\{(?P<keys>[^}]+)\}",
    )

    def _replace_cite(match: re.Match[str]) -> str:
        keys = match.group("keys")
        resolved: List[str] = []
        for raw_key in keys.split(","):
            key = raw_key.strip()
            if not key:
                continue
            entry = _resolve_entry(key)
            if entry:
                resolved.append(_mark_used(entry))
            else:
                resolved.append(key)
        if not resolved:
            return match.group(0)
        cmd = match.group("cmd")
        opts = match.group("opts") or ""
        return f"{cmd}{opts}{{{', '.join(resolved)}}}"

    def _replace_footnote(match: re.Match[str]) -> str:
        body = match.group(1)
        tokens = re.findall(r"Source:\s*([^\s}]+)", body)
        resolved: List[str] = []
        for token in tokens:
            entry = _resolve_entry(token)
            if entry:
                resolved.append(_mark_used(entry))
        if resolved:
            return f"\\cite{{{', '.join(resolved)}}}"
        return ""

    text = cite_pattern.sub(_replace_cite, text)
    text = re.sub(r"\\footnote(?:\[[^\]]*\])?\{(.*?)\}", _replace_footnote, text, flags=re.S)

    if used_keys:
        refs_path = out_dir / "refs.bib"
        refs_path.parent.mkdir(parents=True, exist_ok=True)
        lines: List[str] = []
        for key in used_keys:
            entry = by_cite_key.get(key)
            if not entry:
                continue
            source_path = entry.get("source_path", "") or "unknown"
            filename = escape_latex(Path(source_path).name)
            loc = entry.get("loc", "")
            page = entry.get("page", "")
            chunk = entry.get("chunk", "")
            excerpt = escape_latex(entry.get("excerpt", ""))
            note_parts = []
            slide = extract_slide(loc)
            if slide:
                note_parts.append(f"slide {slide}")
            elif page:
                note_parts.append(f"str. {page}")
            if chunk:
                note_parts.append(f"chunk {chunk}")
            if excerpt:
                note_parts.append(f"\"{excerpt}\"")
            note = ", ".join(note_parts)
            lines.append(f"@misc{{{key},")
            lines.append(f"  title={{{filename}}},")
            if note:
                lines.append(f"  note={{{note}}},")
            lines.append("}")
            lines.append("")
        refs_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    return text

def _wrap_math_commands_outside_math(text: str) -> str:
    targets = {"\\rightarrow", "\\leftarrow", "\\leftrightarrow", "\\Rightarrow", "\\Leftarrow", "\\Leftrightarrow"}
    out = []
    i = 0
    in_math = False
    in_math_env = None
    while i < len(text):
        if text.startswith("\\begin{", i):
            end = text.find("}", i + 7)
            if end != -1:
                env = text[i + 7 : end]
                if env in {
                    "equation",
                    "equation*",
                    "align",
                    "align*",
                    "gather",
                    "gather*",
                    "multline",
                    "multline*",
                    "eqnarray",
                    "eqnarray*",
                    "cases",
                }:
                    in_math_env = env
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if text.startswith("\\end{", i):
            end = text.find("}", i + 5)
            if end != -1:
                env = text[i + 5 : end]
                if in_math_env == env:
                    in_math_env = None
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if text.startswith("\\[", i) or text.startswith("\\(", i):
            in_math = True
            out.append(text[i : i + 2])
            i += 2
            continue
        if text.startswith("\\]", i) or text.startswith("\\)", i):
            in_math = False
            out.append(text[i : i + 2])
            i += 2
            continue
        if text.startswith("$$", i):
            in_math = not in_math
            out.append("$$")
            i += 2
            continue
        if text[i] == "$":
            in_math = not in_math
            out.append("$")
            i += 1
            continue
        if not in_math and in_math_env is None:
            for cmd in targets:
                if text.startswith(cmd, i):
                    out.append(f"${cmd}$")
                    i += len(cmd)
                    break
            else:
                out.append(text[i])
                i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _wrap_mathcal_outside_math(text: str) -> str:
    def _find_matching_brace(s: str, start: int) -> int:
        depth = 0
        for idx in range(start, len(s)):
            if s[idx] == "{":
                depth += 1
            elif s[idx] == "}":
                depth -= 1
                if depth == 0:
                    return idx
        return -1

    out = []
    i = 0
    in_math = False
    in_math_env = None
    in_verbatim = False
    verbatim_envs = {"verbatim", "lstlisting", "PromptBlock"}
    math_envs = {
        "equation",
        "equation*",
        "align",
        "align*",
        "gather",
        "gather*",
        "multline",
        "multline*",
        "eqnarray",
        "eqnarray*",
    }
    while i < len(text):
        if text.startswith("\\begin{", i):
            end = text.find("}", i + 7)
            if end != -1:
                env = text[i + 7 : end]
                if env in verbatim_envs:
                    in_verbatim = True
                if env in math_envs:
                    in_math_env = env
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if text.startswith("\\end{", i):
            end = text.find("}", i + 5)
            if end != -1:
                env = text[i + 5 : end]
                if env in verbatim_envs:
                    in_verbatim = False
                if in_math_env == env:
                    in_math_env = None
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if in_verbatim:
            out.append(text[i])
            i += 1
            continue
        if text.startswith("\\[", i) or text.startswith("\\(", i):
            in_math = True
            out.append(text[i : i + 2])
            i += 2
            continue
        if text.startswith("\\]", i) or text.startswith("\\)", i):
            in_math = False
            out.append(text[i : i + 2])
            i += 2
            continue
        if text.startswith("$$", i):
            in_math = not in_math
            out.append("$$")
            i += 2
            continue
        if text[i] == "$":
            in_math = not in_math
            out.append("$")
            i += 1
            continue

        if not in_math and in_math_env is None:
            if text.startswith("\\ensuremath{", i):
                start = i + len("\\ensuremath")
                end = _find_matching_brace(text, start)
                if end != -1:
                    out.append(text[i : end + 1])
                    i = end + 1
                    continue
            if text.startswith("\\mathcal{", i):
                start = i + len("\\mathcal")
                end = _find_matching_brace(text, start)
                if end != -1:
                    inner = text[i : end + 1]
                    out.append(f"\\ensuremath{{{inner}}}")
                    i = end + 1
                    continue

        out.append(text[i])
        i += 1
    return "".join(out)


def _normalize_unicode_math_symbols(text: str) -> str:
    text = re.sub(r"([A-Za-z\u0391-\u03A9\u03B1-\u03C9])\u20d7", r"\\vec{\1}", text)
    replacements = {
        "\u2212": "-",
        "\u2264": "<=",
        "\u2265": ">=",
        "\u2260": "!=",
        "\u2248": "~=",
        "\u2261": r"\equiv",
        "\u00b7": r"\cdot",
        "\u22c5": r"\cdot",
        "\u00d7": r"\times",
        "\u2202": r"\partial",
        "\u2207": r"\nabla",
        "\u222b": r"\int",
        "\u222c": r"\iint",
        "\u222e": r"\oint",
        "\u2208": r"\in",
        "\u2209": r"\notin",
        "\u221e": r"\infty",
        "\u2211": r"\sum",
        "\u221d": r"\propto",
        "\u2297": r"\otimes",
        "\u2192": r"\rightarrow",
        "\u2190": r"\leftarrow",
        "\u2194": r"\leftrightarrow",
        "\u21a6": r"\mapsto",
        "\u21d2": r"\Rightarrow",
        "\u21d0": r"\Leftarrow",
        "\u21d4": r"\Leftrightarrow",
        "\u226a": r"\ll",
        "\u226b": r"\gg",
        "\u223c": r"\sim",
        "\u224d": r"\asymp",
        "\u2225": r"\parallel",
        "\u2272": r"\lesssim",
        "\u211d": r"\mathbb{R}",
        "\u2124": r"\mathbb{Z}",
        "\u2113": r"\ell",
        "\u0393": r"\Gamma",
        "\u0394": r"\Delta",
        "\u0398": r"\Theta",
        "\u039b": r"\Lambda",
        "\u03a3": r"\Sigma",
        "\u03a6": r"\Phi",
        "\u03a8": r"\Psi",
        "\u03a9": r"\Omega",
        "\u03b1": r"\alpha",
        "\u03b2": r"\beta",
        "\u03b3": r"\gamma",
        "\u03b4": r"\delta",
        "\u03b5": r"\epsilon",
        "\u03b6": r"\zeta",
        "\u03b7": r"\eta",
        "\u03b8": r"\theta",
        "\u03ba": r"\kappa",
        "\u03bb": r"\lambda",
        "\u03bc": r"\mu",
        "\u03bd": r"\nu",
        "\u03be": r"\xi",
        "\u03c0": r"\pi",
        "\u03c1": r"\rho",
        "\u03c3": r"\sigma",
        "\u03c4": r"\tau",
        "\u03c6": r"\phi",
        "\u03c7": r"\chi",
        "\u03c8": r"\psi",
        "\u03c9": r"\omega",
        "\u0435": "e",
        "\U0001d705": r"\kappa",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = text.replace("\u2032", "'")
    return text


_SUPERSCRIPT_TRANSLATION = str.maketrans(
    {
        "\u2070": "0",
        "\u00b9": "1",
        "\u00b2": "2",
        "\u00b3": "3",
        "\u2074": "4",
        "\u2075": "5",
        "\u2076": "6",
        "\u2077": "7",
        "\u2078": "8",
        "\u2079": "9",
        "\u207b": "-",
        "\u207a": "+",
    }
)


def _normalize_superscript_text(text: str) -> str:
    def _repl(match: re.Match[str]) -> str:
        base = match.group("base")
        supers = match.group("supers").translate(_SUPERSCRIPT_TRANSLATION)
        return f"{base}\\textsuperscript{{{supers}}}"

    return re.sub(r"(?P<base>[A-Za-z])(?P<supers>[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+)", _repl, text)


def _normalize_textbar_markup(text: str) -> str:
    text = text.replace(r"\textbar\^{}3", r"|^3")
    text = text.replace(r"\textbar{}", "|")
    return text


def _normalize_math_set_literals(text: str) -> str:
    pattern = re.compile(r"\\in\s*\\?\{([^}]*)\}")

    def _repl(match: re.Match) -> str:
        content = match.group(1)
        cleaned = content.replace('"', "").replace("'", "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return r"\in\{" + cleaned + r"\}"

    return pattern.sub(_repl, text)


_MATH_SUFFIX_COMMANDS = sorted(
    {
        "notin",
        "int",
        "oint",
        "iint",
        "iiint",
        "sum",
        "equiv",
        "infty",
        "cdot",
        "times",
        "propto",
        "otimes",
        "sim",
        "asymp",
        "parallel",
        "lesssim",
        "ell",
        "partial",
        "nabla",
        "Gamma",
        "Delta",
        "Theta",
        "Lambda",
        "Sigma",
        "Phi",
        "Psi",
        "Omega",
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "zeta",
        "eta",
        "theta",
        "kappa",
        "lambda",
        "mu",
        "nu",
        "xi",
        "pi",
        "rho",
        "sigma",
        "tau",
        "phi",
        "varphi",
        "chi",
        "psi",
        "omega",
        "rightarrow",
        "leftarrow",
        "leftrightarrow",
        "Rightarrow",
        "Leftarrow",
        "Leftrightarrow",
        "mapsto",
        "ll",
        "gg",
        "quad",
        "qquad",
    },
    key=len,
    reverse=True,
)


def _separate_math_command_suffixes(text: str) -> str:
    # Insert a space after math operators when immediately followed by a letter/number.
    pattern = re.compile(
        r"\\(" + "|".join(re.escape(cmd) for cmd in _MATH_SUFFIX_COMMANDS) + r")(?=[A-Za-z0-9])"
    )
    return pattern.sub(r"\\\1 ", text)


def _separate_membership_command_suffixes(text: str) -> str:
    return re.sub(r"\\in(?!t)(?!fty)(?=[A-Za-z0-9])", r"\\in ", text)


def _escape_hash_outside_math(text: str) -> str:
    out = []
    i = 0
    in_verbatim = False
    verbatim_envs = {"verbatim", "lstlisting", "PromptBlock"}
    while i < len(text):
        if text.startswith("\\begin{", i):
            end = text.find("}", i + 7)
            if end != -1:
                env = text[i + 7 : end]
                if env in verbatim_envs:
                    in_verbatim = True
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if text.startswith("\\end{", i):
            end = text.find("}", i + 5)
            if end != -1:
                env = text[i + 5 : end]
                if env in verbatim_envs:
                    in_verbatim = False
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if in_verbatim:
            out.append(text[i])
            i += 1
            continue
        if text[i] == "#":
            j = i
            while j < len(text) and text[j] == "#":
                j += 1
            out.append("\\#" * (j - i))
            i = j
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _unescape_angle_brackets_outside_math(text: str) -> str:
    out = []
    i = 0
    in_math = False
    in_math_env = None
    in_verbatim = False
    verbatim_envs = {"verbatim", "lstlisting", "PromptBlock", "tabbing"}
    math_envs = {
        "equation",
        "equation*",
        "align",
        "align*",
        "gather",
        "gather*",
        "multline",
        "multline*",
        "eqnarray",
        "eqnarray*",
    }
    while i < len(text):
        if text.startswith("\\begin{", i):
            end = text.find("}", i + 7)
            if end != -1:
                env = text[i + 7 : end]
                if env in verbatim_envs:
                    in_verbatim = True
                if env in math_envs:
                    in_math_env = env
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if text.startswith("\\end{", i):
            end = text.find("}", i + 5)
            if end != -1:
                env = text[i + 5 : end]
                if env in verbatim_envs:
                    in_verbatim = False
                if in_math_env == env:
                    in_math_env = None
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if in_verbatim:
            out.append(text[i])
            i += 1
            continue
        if text.startswith("\\[", i) or text.startswith("\\(", i):
            in_math = True
            out.append(text[i : i + 2])
            i += 2
            continue
        if text.startswith("\\]", i) or text.startswith("\\)", i):
            in_math = False
            out.append(text[i : i + 2])
            i += 2
            continue
        if text.startswith("$$", i):
            in_math = not in_math
            out.append("$$")
            i += 2
            continue
        if text[i] == "$":
            in_math = not in_math
            out.append("$")
            i += 1
            continue
        if not in_math and in_math_env is None:
            if text.startswith("\\<", i):
                out.append("<")
                i += 2
                continue
            if text.startswith("\\>", i):
                out.append(">")
                i += 2
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


_MATH_INLINE_COMMANDS = {
    "in",
    "notin",
    "sum",
    "int",
    "oint",
    "iint",
    "iiint",
    "equiv",
    "infty",
    "partial",
    "nabla",
    "bar",
    "overline",
    "underline",
    "tau",
    "Gamma",
    "Delta",
    "Theta",
    "Lambda",
    "Sigma",
    "Phi",
    "Psi",
    "Omega",
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
    "eta",
    "theta",
    "kappa",
    "lambda",
    "mu",
    "nu",
    "xi",
    "pi",
    "rho",
    "sigma",
    "phi",
    "chi",
    "psi",
    "omega",
    "mathcal",
    "mathbb",
    "mathbf",
    "mathrm",
    "dot",
    "ddot",
    "vec",
    "hat",
    "tilde",
    "cdot",
    "times",
    "propto",
    "otimes",
    "sim",
    "asymp",
    "parallel",
    "lesssim",
    "ell",
    "sin",
    "cos",
    "tan",
    "cot",
    "sec",
    "csc",
    "ln",
    "log",
    "exp",
    "lim",
    "sup",
    "inf",
    "forall",
    "exists",
    "Re",
    "Im",
    "det",
    "angle",
    "le",
    "ge",
    "neq",
    "approx",
    "ll",
    "gg",
    "pm",
    "frac",
    "tfrac",
    "dfrac",
    "sqrt",
    "left",
    "right",
    "big",
    "Big",
    "bigl",
    "bigr",
    "Bigl",
    "Bigr",
    "bigg",
    "Bigg",
    "biggl",
    "biggr",
    "Biggl",
    "Biggr",
    "lvert",
    "rvert",
    "lVert",
    "rVert",
    "mathrm",
    "operatorname",
    "text",
    "displaystyle",
    "quad",
    "qquad",
    "rightarrow",
    "leftarrow",
    "leftrightarrow",
    "Rightarrow",
    "Leftarrow",
    "Leftrightarrow",
    "mapsto",
    "xrightarrow",
    "xleftarrow",
    "xleftrightarrow",
}

_FRAGMENTED_STYLE_COMMANDS = (
    "symbfit",
    "mathbf",
    "mathit",
    "mathrm",
    "mathsf",
    "mathtt",
    "mathcal",
    "mathbb",
)

_FRAGMENTED_ACCENT_COMMANDS = (
    "hat",
    "bar",
    "tilde",
    "vec",
    "dot",
    "ddot",
    "breve",
    "check",
    "acute",
    "grave",
    "underline",
    "overline",
    "widehat",
    "widetilde",
)

_REPAIRABLE_MATH_COMMANDS = _MATH_INLINE_COMMANDS | {
    "left",
    "right",
    "big",
    "Big",
    "bigl",
    "bigr",
    "Bigl",
    "Bigr",
    "bigg",
    "Bigg",
    "biggl",
    "biggr",
    "Biggl",
    "Biggr",
    "lvert",
    "rvert",
    "lVert",
    "rVert",
}


def _neutralize_placeholder_cites(text: str) -> str:
    cite_re = re.compile(r"\\cite[a-zA-Z*]*(?:\[[^\]]*\]\s*)*\{([^}]+)\}")

    def _is_placeholder(key: str) -> bool:
        lowered = key.lower()
        if "placeholder" in lowered or "bibkey" in lowered or "<" in key or ">" in key:
            return True
        if re.fullmatch(r"\d+(?:-\d+)+", key) or re.fullmatch(r"\d+", key):
            return True
        return False

    def _escape_braces(value: str) -> str:
        return value.replace("{", "\\{").replace("}", "\\}")

    def _repl(match: re.Match) -> str:
        key_blob = match.group(1)
        keys = [k.strip() for k in re.split(r"\s*[,;]\s*", key_blob) if k.strip()]
        if not keys:
            return match.group(0)
        placeholders = [k for k in keys if _is_placeholder(k)]
        if not placeholders:
            return match.group(0)
        remaining = [k for k in keys if not _is_placeholder(k)]
        if remaining:
            return match.group(0).replace(key_blob, ", ".join(remaining))
        return r"\texttt{\textbackslash cite\{" + _escape_braces(key_blob) + r"\}}"

    return cite_re.sub(_repl, text)


def _fix_escaped_quotes(text: str) -> str:
    out = []
    i = 0
    in_verbatim = False
    verbatim_envs = {"verbatim", "lstlisting", "PromptBlock"}
    while i < len(text):
        if text.startswith("\\begin{", i):
            end = text.find("}", i + 7)
            if end != -1:
                env = text[i + 7 : end]
                if env in verbatim_envs:
                    in_verbatim = True
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if text.startswith("\\end{", i):
            end = text.find("}", i + 5)
            if end != -1:
                env = text[i + 5 : end]
                if env in verbatim_envs:
                    in_verbatim = False
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if in_verbatim:
            out.append(text[i])
            i += 1
            continue
        if text.startswith('\\"', i):
            next_char = text[i + 2] if i + 2 < len(text) else ""
            if not next_char or not next_char.isalpha():
                out.append('"')
                i += 2
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _replace_ascii_quotes(text: str) -> str:
    out = []
    i = 0
    in_verbatim = False
    verbatim_envs = {"verbatim", "lstlisting", "PromptBlock"}
    open_quote = True
    while i < len(text):
        if text.startswith("\\begin{", i):
            end = text.find("}", i + 7)
            if end != -1:
                env = text[i + 7 : end]
                if env in verbatim_envs:
                    in_verbatim = True
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if text.startswith("\\end{", i):
            end = text.find("}", i + 5)
            if end != -1:
                env = text[i + 5 : end]
                if env in verbatim_envs:
                    in_verbatim = False
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if in_verbatim:
            out.append(text[i])
            i += 1
            continue
        if text.startswith('\\"', i):
            out.append("``" if open_quote else "''")
            open_quote = not open_quote
            i += 2
            continue
        if text[i] == '"':
            out.append("``" if open_quote else "''")
            open_quote = not open_quote
            i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _count_tabular_columns(spec: str) -> int:
    def _expand_star(s: str) -> str:
        pattern = re.compile(r"\*{(\d+)}{([^}]*)}")
        while True:
            match = pattern.search(s)
            if not match:
                break
            count = int(match.group(1))
            repl = match.group(2) * max(0, count)
            s = s[: match.start()] + repl + s[match.end() :]
        return s

    cleaned = _expand_star(spec)
    cleaned = re.sub(r"@{[^}]*}", "", cleaned)
    cleaned = re.sub(r"[<>!]{[^}]*}", "", cleaned)
    cleaned = re.sub(r"[| ]+", "", cleaned)
    cleaned = re.sub(r"p{[^}]*}", "p", cleaned)
    cleaned = re.sub(r"m{[^}]*}", "m", cleaned)
    cleaned = re.sub(r"b{[^}]*}", "b", cleaned)
    cols = re.findall(r"[lcrXSpmb]", cleaned)
    return max(1, len(cols))


def _fix_tabular_alignment(text: str) -> str:
    begin_tag = "\\begin{tabular}"
    end_tag = "\\end{tabular}"
    out = []
    i = 0
    while True:
        idx = text.find(begin_tag, i)
        if idx == -1:
            out.append(text[i:])
            break
        out.append(text[i:idx])
        spec_start = text.find("{", idx + len(begin_tag))
        if spec_start == -1:
            out.append(text[idx:])
            break
        depth = 0
        j = spec_start
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            out.append(text[idx:])
            break
        spec = text[spec_start + 1 : j]
        end_idx = text.find(end_tag, j + 1)
        if end_idx == -1:
            out.append(text[idx:])
            break
        body = text[j + 1 : end_idx]
        expected = _count_tabular_columns(spec)
        max_tabs = max(0, expected - 1)

        parts = re.split(r"(\\\\)", body)
        for k in range(0, len(parts), 2):
            row = parts[k]
            if not row.strip():
                continue
            stripped = row.lstrip()
            if stripped.startswith("\\hline") or stripped.startswith("\\cline"):
                continue
            tabs = 0
            fixed = []
            pos = 0
            while pos < len(row):
                ch = row[pos]
                if ch == "&" and (pos == 0 or row[pos - 1] != "\\"):
                    tabs += 1
                    if tabs > max_tabs:
                        fixed.append(r"\&")
                    else:
                        fixed.append("&")
                    pos += 1
                    continue
                fixed.append(ch)
                pos += 1
            parts[k] = "".join(fixed)
        fixed_body = "".join(parts)
        out.append(f"{begin_tag}{{{spec}}}{fixed_body}{end_tag}")
        i = end_idx + len(end_tag)
    return "".join(out)


def _wrap_inline_math_tokens_outside_math(text: str) -> str:
    block_re = re.compile(
        r"("
        r"\\begin\{(?:verbatim|lstlisting|PromptBlock)\}.*?\\end\{(?:verbatim|lstlisting|PromptBlock)\}"
        r"|\\begin\{(?:equation\*?|align\*?|gather\*?|multline\*?|eqnarray\*?|cases)\}.*?"
        r"\\end\{(?:equation\*?|align\*?|gather\*?|multline\*?|eqnarray\*?|cases)\}"
        r"|\\\[[\s\S]*?\\\]"
        r"|\\\([\s\S]*?\\\)"
        r"|\$\$[\s\S]*?\$\$"
        r"|\$[^$]*?\$"
        r"|\\ensuremath\{.*?\}"
        r"|\\texttt\{.*?\}"
        r"|\\url\{.*?\}"
        r")",
        re.S,
    )

    def _is_math_trigger(seg: str, idx: int) -> bool:
        if idx >= len(seg):
            return False
        if seg[idx] == "\\":
            j = idx + 1
            while j < len(seg) and seg[j].isalpha():
                j += 1
            if j == idx + 1:
                return False
            cmd = seg[idx + 1 : j]
            return cmd in _MATH_INLINE_COMMANDS
        return False

    def _wrap_segment(seg: str) -> str:
        if "\\" not in seg:
            return seg
        out: List[str] = []
        i = 0
        in_math = False
        brace_depth = 0
        while i < len(seg):
            ch = seg[i]
            if not in_math:
                if _is_math_trigger(seg, i):
                    out.append("$")
                    in_math = True
                    continue
                out.append(ch)
                i += 1
                continue

            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                if brace_depth > 0:
                    brace_depth -= 1
            if ch in "\r\n":
                out.append("$")
                in_math = False
                out.append(ch)
                i += 1
                continue
            if brace_depth == 0 and ch in ",;:":
                out.append("$")
                in_math = False
                continue
            if brace_depth == 0 and ch == ".":
                if not (i > 0 and seg[i - 1].isdigit() and i + 1 < len(seg) and seg[i + 1].isdigit()):
                    out.append("$")
                    in_math = False
                    continue
            if brace_depth == 0 and ch.isspace():
                j = i
                while j < len(seg) and seg[j].isspace():
                    if seg[j] in "\r\n":
                        break
                    j += 1
                if j >= len(seg):
                    out.append("$")
                    in_math = False
                    out.append(seg[i:])
                    break
                nxt = seg[j]
                if nxt.isalpha():
                    k = j
                    while k < len(seg) and seg[k].isalpha():
                        k += 1
                    token = seg[j:k]
                    if len(token) == 1:
                        follow = seg[k] if k < len(seg) else ""
                        if follow in {"_", "^", "\\"}:
                            out.append(ch)
                            i += 1
                            continue
                    out.append("$")
                    in_math = False
                    continue
            out.append(ch)
            i += 1

        if in_math:
            out.append("$")
        return "".join(out)

    parts = block_re.split(text)
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            parts[idx] = part
        else:
            parts[idx] = _wrap_segment(part)
    return "".join(parts)


def _escape_alignment_tabs_outside_math(text: str) -> str:
    out = []
    i = 0
    in_math = False
    in_math_env = None
    in_verbatim = False
    verbatim_envs = {"verbatim", "lstlisting", "PromptBlock"}
    tabular_envs = {"tabular", "tabular*", "array", "align", "align*", "eqnarray", "eqnarray*"}
    while i < len(text):
        if text.startswith("\\begin{", i):
            end = text.find("}", i + 7)
            if end != -1:
                env = text[i + 7 : end]
                if env in verbatim_envs:
                    in_verbatim = True
                if env in tabular_envs:
                    in_math_env = env
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if text.startswith("\\end{", i):
            end = text.find("}", i + 5)
            if end != -1:
                env = text[i + 5 : end]
                if env in verbatim_envs:
                    in_verbatim = False
                if in_math_env == env:
                    in_math_env = None
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if in_verbatim:
            out.append(text[i])
            i += 1
            continue
        if text.startswith("\\[", i) or text.startswith("\\(", i):
            in_math = True
            out.append(text[i : i + 2])
            i += 2
            continue
        if text.startswith("\\]", i) or text.startswith("\\)", i):
            in_math = False
            out.append(text[i : i + 2])
            i += 2
            continue
        if text.startswith("$$", i):
            in_math = not in_math
            out.append("$$")
            i += 2
            continue
        if text[i] == "$":
            in_math = not in_math
            out.append("$")
            i += 1
            continue
        if text[i] == "&" and not in_math and in_math_env is None:
            out.append("\\&")
            i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _fix_linebreak_before_ampersand(text: str) -> str:
    out = []
    i = 0
    in_math = False
    in_math_env = None
    in_verbatim = False
    verbatim_envs = {"verbatim", "lstlisting", "PromptBlock"}
    tabular_envs = {"tabular", "tabular*", "array", "align", "align*", "eqnarray", "eqnarray*"}
    while i < len(text):
        if text.startswith("\\begin{", i):
            end = text.find("}", i + 7)
            if end != -1:
                env = text[i + 7 : end]
                if env in verbatim_envs:
                    in_verbatim = True
                if env in tabular_envs:
                    in_math_env = env
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if text.startswith("\\end{", i):
            end = text.find("}", i + 5)
            if end != -1:
                env = text[i + 5 : end]
                if env in verbatim_envs:
                    in_verbatim = False
                if in_math_env == env:
                    in_math_env = None
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if in_verbatim:
            out.append(text[i])
            i += 1
            continue
        if text.startswith("\\[", i) or text.startswith("\\(", i):
            in_math = True
            out.append(text[i : i + 2])
            i += 2
            continue
        if text.startswith("\\]", i) or text.startswith("\\)", i):
            in_math = False
            out.append(text[i : i + 2])
            i += 2
            continue
        if text.startswith("$$", i):
            in_math = not in_math
            out.append("$$")
            i += 2
            continue
        if text[i] == "$":
            in_math = not in_math
            out.append("$")
            i += 1
            continue
        if not in_math and in_math_env is None and text.startswith("\\\\&", i):
            out.append("\\\\ \\&")
            i += 3
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _fix_linebreak_before_hash(text: str) -> str:
    # Normalize any backslashes immediately before hashes to escaped hashes.
    # Example: "\\\\###" -> "\#\#\#"
    def _repl(match: re.Match) -> str:
        hashes = match.group(1)
        return "\\#" * len(hashes)

    parts = re.split(
        r"(\\begin\{(?:verbatim|lstlisting)\}.*?\\end\{(?:verbatim|lstlisting)\})",
        text,
        flags=re.S,
    )
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"\\+(#+)", _repl, parts[i])
    return "".join(parts)


def _close_unmatched_math_envs(text: str) -> str:
    envs = [
        "equation",
        "equation*",
        "align",
        "align*",
        "gather",
        "gather*",
        "multline",
        "multline*",
        "eqnarray",
        "eqnarray*",
    ]
    stack = []
    i = 0
    while i < len(text):
        if text.startswith("\\begin{", i):
            end = text.find("}", i + 7)
            if end != -1:
                env = text[i + 7 : end]
                if env in envs:
                    stack.append(env)
                i = end + 1
                continue
        if text.startswith("\\end{", i):
            end = text.find("}", i + 5)
            if end != -1:
                env = text[i + 5 : end]
                if env in envs:
                    if stack and stack[-1] == env:
                        stack.pop()
                i = end + 1
                continue
        i += 1
    if not stack:
        return text
    suffix = "".join(f"\n\\end{{{env}}}" for env in reversed(stack))
    return text + suffix


def _strip_blank_lines_in_math_envs(text: str) -> str:
    envs = {
        "equation",
        "equation*",
        "align",
        "align*",
        "gather",
        "gather*",
        "multline",
        "multline*",
        "eqnarray",
        "eqnarray*",
    }
    lines = text.splitlines()
    out_lines = []
    depth = 0
    for line in lines:
        begins = re.findall(r"\\begin\{([^\}]+)\}", line)
        ends = re.findall(r"\\end\{([^\}]+)\}", line)
        for env in begins:
            if env in envs:
                depth += 1
        if depth > 0 and (not line.strip() or line.strip() == r"\par"):
            # Blank line or \par inside math environment breaks align-like blocks.
            continue
        out_lines.append(line)
        for env in ends:
            if env in envs and depth > 0:
                depth -= 1
    return "\n".join(out_lines)


def _replace_nonascii_lstlisting(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "\\begin{lstlisting}" in line:
            # Capture begin line and optional options line if split.
            begin_line = line
            body_lines: List[str] = []
            i += 1
            while i < len(lines):
                if "\\end{lstlisting}" in lines[i]:
                    end_line = lines[i]
                    body = "".join(body_lines)
                    if any(ord(ch) > 127 for ch in body):
                        out.append("\\begin{verbatim}\n")
                        out.append(body)
                        out.append("\\end{verbatim}\n")
                    else:
                        out.append(begin_line)
                        out.extend(body_lines)
                        out.append(end_line)
                    i += 1
                    break
                body_lines.append(lines[i])
                i += 1
            else:
                # Unterminated lstlisting; emit as-is to avoid dropping content.
                out.append(begin_line)
                out.extend(body_lines)
            continue
        out.append(line)
        i += 1
    return "".join(out)


def _sanitize_lstlisting_language(text: str) -> str:
    def _normalize_lang(match: re.Match) -> str:
        lang = match.group(1)
        known = {
            "python",
            "json",
            "yaml",
            "yml",
            "xml",
            "html",
            "css",
            "javascript",
            "js",
            "typescript",
            "ts",
            "bash",
            "sh",
            "powershell",
            "ps",
            "sql",
            "c",
            "cpp",
            "java",
        }
        if lang.lower() not in known:
            return ""
        return match.group(0)

    # Drop unsupported listing languages to avoid Listings errors.
    text = re.sub(r"\[language\s*=\s*([A-Za-z0-9_+-]+)\]", _normalize_lang, text)
    # Also remove language from \lstset{...} blocks.
    text = re.sub(r"(\\lstset\{[^}]*)language\s*=\s*[^,}]+,?", r"\1", text)
    return text


def _strip_linebreaks_at_line_start(text: str) -> str:
    parts = re.split(
        r"(\\begin\{(?:verbatim|lstlisting)\}.*?\\end\{(?:verbatim|lstlisting)\})",
        text,
        flags=re.S,
    )
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"(?m)^[ \t]*\\\\(\[[^\]]+\])?[ \t]*\n", "", parts[i])
    return "".join(parts)


def _strip_linebreaks_after_item_label(text: str) -> str:
    parts = re.split(
        r"(\\begin\{(?:verbatim|lstlisting)\}.*?\\end\{(?:verbatim|lstlisting)\})",
        text,
        flags=re.S,
    )
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"(\\item\\[[^\\]]+\\]:)[ \t]*\\\\(\\[[^\\]]+\\])?", r"\\1", parts[i])
    return "".join(parts)


def _fix_htmlish_end_tags(text: str) -> str:
    return re.sub(r"</begin\{([^}]+)\}>?", r"\\end{\1}", text)


def _isolate_prompt_blocks(text: str) -> str:
    text = re.sub(r"(?m)(?<!^)([^\n])\\begin\{PromptBlock\}", r"\1\n\\begin{PromptBlock}", text)
    text = re.sub(r"(?m)\\begin\{PromptBlock\}[ \t]*", r"\\begin{PromptBlock}\n", text)
    text = re.sub(r"(?m)\\end\{PromptBlock\}[ \t]*", r"\\end{PromptBlock}\n", text)
    text = re.sub(r"(?m)\\end\{PromptBlock\}([^\n]+)", r"\\end{PromptBlock}\n\\1", text)
    return text


def _collapse_repeated_linebreaks(text: str) -> str:
    parts = re.split(
        r"(\\begin\{(?:verbatim|lstlisting|PromptBlock)\}.*?\\end\{(?:verbatim|lstlisting|PromptBlock)\})",
        text,
        flags=re.S,
    )
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"\\\\(?:\s*\\\\)+", r"\\\\", parts[i])
        parts[i] = re.sub(r"(?m)^[ \t]*\\\\[ \t]*$", "", parts[i])
    return "".join(parts)


def _move_trailing_citations_out_of_math(text: str) -> str:
    cite_re = r"\\cite[a-zA-Z*]*(?:\[[^\]]*\]\s*)*\{[^}]*\}"

    text = re.sub(
        rf"\\\[(?P<body>[\s\S]*?)\s*(?P<cite>{cite_re})\s*\\\]",
        lambda m: rf"\[ {m.group('body').rstrip()} \] {m.group('cite')}",
        text,
    )
    text = re.sub(
        rf"\\\((?P<body>[\s\S]*?)\s*(?P<cite>{cite_re})\s*\\\)",
        lambda m: rf"\( {m.group('body').rstrip()} \) {m.group('cite')}",
        text,
    )
    return text


def _needs_prompt_block(content: str, threshold: int) -> bool:
    if len(content) > threshold:
        return True
    if re.search(r"\S{40,}", content):
        return True
    if re.search(r"https?://\S+", content):
        return True
    return False


def _inline_to_prompt_block(content: str, original: str, threshold: int) -> str:
    if not _needs_prompt_block(content, threshold):
        return original
    return "\n\\begin{PromptBlock}\n" + content + "\n\\end{PromptBlock}\n"


def _convert_long_inline_code(text: str, threshold: int = 80) -> str:
    parts = re.split(
        r"(\\begin\{(?:verbatim|lstlisting|PromptBlock)\}.*?\\end\{(?:verbatim|lstlisting|PromptBlock)\})",
        text,
        flags=re.S,
    )

    def _convert_segment(segment: str) -> str:
        segment = re.sub(
            r"\\texttt\{([^{}]*)\}",
            lambda m: _inline_to_prompt_block(m.group(1), m.group(0), threshold),
            segment,
        )
        segment = re.sub(
            r"\\lstinline(?:\[[^\]]*\])?(?P<delim>[^a-zA-Z0-9])(?P<content>.*?)(?P=delim)",
            lambda m: _inline_to_prompt_block(m.group("content"), m.group(0), threshold),
            segment,
            flags=re.S,
        )
        segment = re.sub(
            r"\\verb\*?(?P<delim>[^a-zA-Z0-9])(?P<content>.*?)(?P=delim)",
            lambda m: _inline_to_prompt_block(m.group("content"), m.group(0), threshold),
            segment,
            flags=re.S,
        )
        return segment

    for i in range(0, len(parts), 2):
        parts[i] = _convert_segment(parts[i])
    return "".join(parts)


_MARKDOWN_INLINE_MATH_COMMANDS = sorted(
    _MATH_INLINE_COMMANDS
    | {
        "rightarrow",
        "leftarrow",
        "leftrightarrow",
        "Rightarrow",
        "Leftarrow",
        "Leftrightarrow",
    },
    key=len,
    reverse=True,
)


def _prepare_markdown_for_latex(markdown_text: str) -> str:
    text = _normalize_unicode_math_symbols(markdown_text)
    text = _normalize_spaced_inline_dollar_math(text)
    text = _repair_split_inline_math_commands(text)
    text = _normalize_escaped_math_subscripts(text)
    text = _strip_math_sizing_delimiters(text)
    text = _normalize_unbraced_style_arguments(text)
    text = _normalize_fragmented_style_math_commands(text)
    text = _normalize_fragmented_accent_math_commands(text)
    text = _normalize_fragmented_inline_fraction_commands(text)
    text = _merge_interleaved_inline_math(text)
    text = _separate_membership_command_suffixes(text)
    text = _separate_math_command_suffixes(text)
    text = _normalize_inline_fraction_commands(text)
    protected_re = re.compile(
        r"("
        r"```[\s\S]*?```"
        r"|`[^`\n]*`"
        r"|\\begin\{(?:verbatim|lstlisting|PromptBlock)\}.*?\\end\{(?:verbatim|lstlisting|PromptBlock)\}"
        r"|\\begin\{(?:equation\*?|align\*?|gather\*?|multline\*?|eqnarray\*?|cases)\}.*?"
        r"\\end\{(?:equation\*?|align\*?|gather\*?|multline\*?|eqnarray\*?|cases)\}"
        r"|\\\[[\s\S]*?\\\]"
        r"|\\\([\s\S]*?\\\)"
        r"|\$\$[\s\S]*?\$\$"
        r"|\$[^$]*?\$"
        r"|\\cite[a-zA-Z*]*(?:\[[^\]]*\]\s*)*\{[^}]*\}"
        r"|\\url\{[^}]*\}"
        r"|\\href\{[^}]*\}\{[^}]*\}"
        r")",
        re.S,
    )
    cmd_names = "|".join(re.escape(name) for name in _MARKDOWN_INLINE_MATH_COMMANDS)
    command_re = re.compile(
        rf"(?<![_^\{{])(?P<expr>\\(?:{cmd_names})(?![A-Za-z])(?:\[[^\[\]]*\])?(?:\{{[^{{}}]*\}}){{0,2}}"
        rf"(?:(?:_\{{[^{{}}]+\}}|_(?:\\[A-Za-z]+)|_[A-Za-z0-9]|\^\{{[^{{}}]+\}}|\^(?:\\[A-Za-z]+)|\^[A-Za-z0-9]))*)"
    )
    symbol_re = re.compile(
        r"(?<![$\\A-Za-z0-9])(?P<expr>[A-Za-z](?:'*)"
        r"(?:(?:_\{[^{}]+\}|_(?:\\[A-Za-z]+)|_[A-Za-z0-9]|\^\{[^{}]+\}|\^(?:\\[A-Za-z]+)|\^[A-Za-z0-9])+))"
    )

    def _wrap_segment(seg: str) -> str:
        if not seg:
            return seg
        seg = re.sub(r"\\[,;:!]", " ", seg)
        seg = re.sub(r"(\\[A-Za-z]+)_([A-Za-z0-9]{2,})", r"\1_{\2}", seg)
        seg = re.sub(r"(\\[A-Za-z]+)\^([A-Za-z0-9]{2,})", r"\1^{\2}", seg)
        seg = re.sub(r"(\b[A-Za-z](?:'*)?)_([A-Za-z0-9]{2,})", r"\1_{\2}", seg)
        seg = re.sub(r"(\b[A-Za-z](?:'*)?)\^([A-Za-z0-9]{2,})", r"\1^{\2}", seg)
        protected_commands: Dict[str, str] = {}

        def _protect_command(match: re.Match) -> str:
            key = f"@@MATHCMD{len(protected_commands)}@@"
            protected_commands[key] = match.group("expr")
            return key

        seg = command_re.sub(_protect_command, seg)
        seg = symbol_re.sub(lambda m: f"${m.group('expr')}$", seg)
        for key, raw_expr in protected_commands.items():
            seg = seg.replace(key, raw_expr)

        parts = re.split(r"(\$[^$]*\$)", seg)
        for part_idx in range(0, len(parts), 2):
            parts[part_idx] = command_re.sub(lambda m: f"${m.group('expr')}$", parts[part_idx])
        seg = "".join(parts)
        return seg

    parts = protected_re.split(text)
    for idx in range(0, len(parts), 2):
        parts[idx] = _wrap_segment(parts[idx])
    return "".join(parts)


def _normalize_inline_fraction_commands(text: str) -> str:
    return re.sub(
        r"\\(?P<cmd>frac|tfrac|dfrac)\s*(?P<num>[A-Za-z0-9])\s*(?P<den>[A-Za-z0-9])",
        lambda m: f"\\{m.group('cmd')}{{{m.group('num')}}}{{{m.group('den')}}}",
        text,
    )


def _normalize_spaced_inline_dollar_math(text: str) -> str:
    math_hint_re = re.compile(r"\\|[_^{}]|[=+\-*/<>]|[∂∇ΓγδμθϕκωΣΠπ∞→←↔≈≠≤≥∈∮∫]")

    def _replace(match: re.Match) -> str:
        body = match.group("body")
        stripped = body.strip()
        if stripped == body or not stripped:
            return match.group(0)
        if not math_hint_re.search(stripped):
            return match.group(0)
        return f"${stripped}$"

    return re.sub(r"(?<!\$)\$(?P<body>[^$\n]+)\$(?!\$)", _replace, text)


def _unwrap_inline_math_fragment(fragment: str) -> str:
    fragment = fragment.strip()
    if fragment.startswith(r"\(") and fragment.endswith(r"\)"):
        return fragment[2:-2].strip()
    if fragment.startswith("$") and fragment.endswith("$"):
        return fragment[1:-1].strip()
    return fragment


def _normalize_unbraced_style_arguments(text: str) -> str:
    style_re = "|".join(_FRAGMENTED_STYLE_COMMANDS)

    text = re.sub(
        rf"\\(?P<cmd>{style_re})\s+\\\((?P<body>[^\n]+?)\\\)",
        lambda m: f"\\{m.group('cmd')}{{{m.group('body').strip()}}}",
        text,
    )
    text = re.sub(
        rf"\\(?P<cmd>{style_re})\s+\$(?P<body>[^$\n]+)\$",
        lambda m: f"\\{m.group('cmd')}{{{m.group('body').strip()}}}",
        text,
    )
    text = re.sub(
        rf"\\(?P<cmd>{style_re})\s+(?P<body>\\[A-Za-z]+(?:\{{[^{{}}]+\}})?|[A-Za-z0-9])(?=(?:[^A-Za-z0-9]|$))",
        lambda m: f"\\{m.group('cmd')}{{{m.group('body').strip()}}}",
        text,
    )
    return text


def _repair_split_inline_math_commands(text: str) -> str:
    def _replace(match: re.Match) -> str:
        prefix = match.group("prefix")[1:]
        suffix = match.group("suffix")
        combined = prefix + suffix
        if combined in _REPAIRABLE_MATH_COMMANDS:
            return "\\" + combined
        return match.group(0)

    return re.sub(r"\\\((?P<prefix>\\[A-Za-z]+)\\\)(?P<suffix>[A-Za-z]+)", _replace, text)


def _normalize_escaped_math_subscripts(text: str) -> str:
    commands = _MATH_INLINE_COMMANDS | set(_MATH_SUFFIX_COMMANDS)
    cmd_re = "|".join(re.escape(cmd) for cmd in sorted(commands, key=len, reverse=True))
    return re.sub(
        rf"\\(?P<cmd>{cmd_re})\\_(?P<sub>\{{[^{{}}]+\}}|[A-Za-z0-9])",
        lambda m: f"\\{m.group('cmd')}_{m.group('sub')}",
        text,
    )


def _strip_math_sizing_delimiters(text: str) -> str:
    delimiter_re = r"(?:[\(\)\[\]\{\}\|\.]|\\lvert|\\rvert|\\lVert|\\rVert)"
    size_cmd_re = r"(?:left|right|big|Big|bigl|bigr|Bigl|Bigr|bigg|Bigg|biggl|biggr|Biggl|Biggr)"
    text = re.sub(rf"\\(?:{size_cmd_re})\s*(?={delimiter_re})", "", text)
    text = re.sub(rf"\\\(\s*\\(?:{size_cmd_re})\s*\\\)\s*(?={delimiter_re})", "", text)
    return text


def _normalize_fragmented_style_math_commands(text: str) -> str:
    style_re = "|".join(_FRAGMENTED_STYLE_COMMANDS)

    def _replace(match: re.Match) -> str:
        body = _unwrap_inline_math_fragment(match.group("body"))
        if not body:
            return match.group(0)
        return f"\\(\\{match.group('cmd')}{{{body}}}\\)"

    return re.sub(
        rf"\\\(\s*\\(?P<cmd>{style_re})\s*\\\)\s*"
        rf"(?P<body>\\\([^\n]+?\\\)|\$[^$\n]+\$|\\[A-Za-z]+(?:\{{[^{{}}]+\}})?|[A-Za-z0-9])",
        _replace,
        text,
    )


def _read_braced_group(value: str, start: int) -> Optional[Tuple[str, int]]:
    escaped = False
    if value.startswith(r"\{", start):
        escaped = True
        start += 2
    elif start < len(value) and value[start] == "{":
        start += 1
    else:
        return None
    depth = 0
    idx = start
    while idx < len(value):
        if escaped:
            if value.startswith(r"\{", idx):
                depth += 1
                idx += 2
                continue
            if value.startswith(r"\}", idx):
                if depth == 0:
                    return value[start:idx], idx + 2
                depth -= 1
                idx += 2
                continue
            idx += 1
            continue
        ch = value[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                return value[start:idx], idx + 1
            depth -= 1
        idx += 1
    return None


def _collapse_inline_math_group(group: str) -> str:
    group = re.sub(r"\\\((.*?)\\\)", lambda m: m.group(1).strip(), group, flags=re.S)
    group = re.sub(r"\$([^$\n]+)\$", lambda m: m.group(1).strip(), group)
    group = group.replace("$", "")
    group = re.sub(r"\s+", " ", group).strip()
    return group


def _normalize_fragmented_accent_math_commands(text: str) -> str:
    accent_re = "|".join(_FRAGMENTED_ACCENT_COMMANDS)

    out: List[str] = []
    i = 0
    while i < len(text):
        match = re.match(rf"\\\(\s*\\(?P<cmd>{accent_re})\s*\\\)", text[i:])
        if not match:
            out.append(text[i])
            i += 1
            continue

        j = i + match.end()
        while j < len(text) and text[j].isspace():
            j += 1
        argument = _read_braced_group(text, j)
        if argument is None:
            out.append(text[i])
            i += 1
            continue

        body, j = argument
        body = _collapse_inline_math_group(body)
        if not body:
            out.append(text[i])
            i += 1
            continue

        out.append(f"\\(\\{match.group('cmd')}{{{body}}}\\)")
        i = j
    return "".join(out)


def _normalize_fragmented_inline_fraction_commands(text: str) -> str:
    out: List[str] = []
    i = 0
    while i < len(text):
        match = re.match(r"\\\(\s*\\(?P<cmd>frac|tfrac|dfrac)\s*\\\)", text[i:])
        if not match:
            out.append(text[i])
            i += 1
            continue

        j = i + match.end()
        while j < len(text) and text[j].isspace():
            j += 1
        numerator = _read_braced_group(text, j)
        if numerator is None:
            out.append(text[i])
            i += 1
            continue

        num_body, j = numerator
        while j < len(text) and text[j].isspace():
            j += 1
        denominator = _read_braced_group(text, j)
        if denominator is None:
            out.append(text[i])
            i += 1
            continue

        den_body, j = denominator
        num_body = _collapse_inline_math_group(num_body)
        den_body = _collapse_inline_math_group(den_body)
        out.append(f"\\(\\{match.group('cmd')}{{{num_body}}}{{{den_body}}}\\)")
        i = j
    return "".join(out)


def _merge_interleaved_inline_math(text: str) -> str:
    previous = None
    while text != previous:
        previous = text
        text = re.sub(r"\\\)\s*\$([^$\n]+)\$\s*\\\(", lambda m: m.group(1).strip(), text)
        text = re.sub(r"\\\)\s*\\\(", "", text)
    return text


def _split_trailing_inline_math_punctuation(body: str) -> Tuple[str, str]:
    trailing = ""
    while body and body[-1] in "),.;:":
        trailing = body[-1] + trailing
        body = body[:-1].rstrip()
    while body.endswith("}") and body.count("{") < body.count("}"):
        body = body[:-1].rstrip()
    return body.rstrip(), trailing


def _normalize_boldsymbol_inline_math(text: str) -> str:
    def _replace_dollar(match: re.Match) -> str:
        body, trailing = _split_trailing_inline_math_punctuation(match.group("body").strip())
        if not body:
            return match.group(0)
        return f"$\\symbfit{{{body}}}${trailing}"

    def _replace_paren(match: re.Match) -> str:
        body, trailing = _split_trailing_inline_math_punctuation(match.group("body").strip())
        if not body:
            return match.group(0)
        return f"\\(\\symbfit{{{body}}}\\){trailing}"

    text = re.sub(r"\\(?:boldsymbol|bm)\s*\$(?P<body>[^$\n]+)\$", _replace_dollar, text)
    text = re.sub(r"\\(?:boldsymbol|bm)\s*\\\((?P<body>[^\n]+?)\\\)", _replace_paren, text)
    return text


def _normalize_styled_math_wrappers(text: str) -> str:
    style_names = (
        "symbfit",
        "mathbf",
        "mathit",
        "mathrm",
        "mathsf",
        "mathtt",
        "mathcal",
        "mathbb",
    )
    style_re = "|".join(style_names)

    def _replace_dollar(match: re.Match) -> str:
        body, trailing = _split_trailing_inline_math_punctuation(match.group("body").strip())
        if not body:
            return match.group(0)
        return f"$\\{match.group('cmd')}{{{body}}}${trailing}"

    def _replace_paren(match: re.Match) -> str:
        body, trailing = _split_trailing_inline_math_punctuation(match.group("body").strip())
        if not body:
            return match.group(0)
        return f"\\(\\{match.group('cmd')}{{{body}}}\\){trailing}"

    text = re.sub(
        rf"\\(?P<cmd>{style_re})\s*\{{\$(?P<body>[^$\n]+)\$(?:\}})?",
        _replace_dollar,
        text,
    )
    text = re.sub(
        rf"\\(?P<cmd>{style_re})\s*\{{\\\((?P<body>[^\n]+?)\\\)(?:\}})?",
        _replace_paren,
        text,
    )
    return text


def _wrap_styled_math_commands_outside_math(text: str) -> str:
    style_cmds = {
        "\\symbfit",
        "\\mathbf",
        "\\mathit",
        "\\mathrm",
        "\\mathsf",
        "\\mathtt",
        "\\mathcal",
        "\\mathbb",
    }
    verbatim_envs = {"verbatim", "lstlisting", "PromptBlock"}
    math_envs = {
        "equation",
        "equation*",
        "align",
        "align*",
        "gather",
        "gather*",
        "multline",
        "multline*",
        "eqnarray",
        "eqnarray*",
        "cases",
    }

    def _find_matching_brace(s: str, start: int) -> int:
        depth = 0
        for idx in range(start, len(s)):
            if s[idx] == "{":
                depth += 1
            elif s[idx] == "}":
                depth -= 1
                if depth == 0:
                    return idx
        return -1

    def _consume_script_suffixes(s: str, start: int) -> int:
        idx = start
        while idx < len(s) and s[idx] in {"_", "^"}:
            marker_idx = idx
            idx += 1
            if idx >= len(s):
                return marker_idx
            if s[idx] == "{":
                end = _find_matching_brace(s, idx)
                if end == -1:
                    return marker_idx
                idx = end + 1
                continue
            if s[idx] == "\\":
                idx += 1
                while idx < len(s) and s[idx].isalpha():
                    idx += 1
                continue
            idx += 1
        return idx

    out: List[str] = []
    i = 0
    in_math = False
    in_math_env = None
    in_verbatim = False
    while i < len(text):
        if text.startswith("\\begin{", i):
            end = text.find("}", i + 7)
            if end != -1:
                env = text[i + 7 : end]
                if env in verbatim_envs:
                    in_verbatim = True
                if env in math_envs:
                    in_math_env = env
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if text.startswith("\\end{", i):
            end = text.find("}", i + 5)
            if end != -1:
                env = text[i + 5 : end]
                if env in verbatim_envs:
                    in_verbatim = False
                if in_math_env == env:
                    in_math_env = None
                out.append(text[i : end + 1])
                i = end + 1
                continue
        if text.startswith("\\[", i) or text.startswith("\\(", i):
            in_math = True
            out.append(text[i : i + 2])
            i += 2
            continue
        if text.startswith("\\]", i) or text.startswith("\\)", i):
            in_math = False
            out.append(text[i : i + 2])
            i += 2
            continue
        if text.startswith("$$", i):
            in_math = not in_math
            out.append("$$")
            i += 2
            continue
        if text[i] == "$":
            in_math = not in_math
            out.append("$")
            i += 1
            continue
        if not in_math and in_math_env is None and not in_verbatim:
            for cmd in style_cmds:
                if text.startswith(f"{cmd}{{", i):
                    arg_start = i + len(cmd)
                    arg_end = _find_matching_brace(text, arg_start)
                    if arg_end != -1:
                        wrap_end = _consume_script_suffixes(text, arg_end + 1)
                        out.append(f"${text[i:wrap_end]}$")
                        i = wrap_end
                        break
            else:
                out.append(text[i])
                i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _sanitize_section_tex(text: str) -> Tuple[str, List[str]]:
    issues: List[str] = []
    original = text
    text = _normalize_boldsymbol_inline_math(text)
    text = _repair_split_inline_math_commands(text)
    text = _normalize_escaped_math_subscripts(text)
    text = _strip_math_sizing_delimiters(text)
    text = _normalize_unbraced_style_arguments(text)
    text = _normalize_fragmented_style_math_commands(text)
    text = _normalize_fragmented_accent_math_commands(text)
    text = _normalize_fragmented_inline_fraction_commands(text)
    text = _merge_interleaved_inline_math(text)
    text = _normalize_inline_fraction_commands(text)
    text = re.sub(r"\\(?:boldsymbol|bm)\s*(\\[A-Za-z]+)", r"\\symbfit{\1}", text)
    text = text.replace(r"\boldsymbol{", r"\symbfit{")
    text = text.replace(r"\bm{", r"\symbfit{")
    text = _normalize_unicode_math_symbols(text)
    text = _normalize_superscript_text(text)
    text = _normalize_textbar_markup(text)
    text = _separate_membership_command_suffixes(text)
    text = _normalize_math_set_literals(text)
    text = _separate_math_command_suffixes(text)
    text = _neutralize_placeholder_cites(text)
    text = _move_trailing_citations_out_of_math(text)
    text = _wrap_math_commands_outside_math(text)
    text = _wrap_mathcal_outside_math(text)
    text = _normalize_styled_math_wrappers(text)
    text = _wrap_styled_math_commands_outside_math(text)
    text = _wrap_inline_math_tokens_outside_math(text)
    text = _escape_caret_outside_math(text)
    text = _escape_hash_outside_math(text)
    text = _unescape_angle_brackets_outside_math(text)
    text = _escape_alignment_tabs_outside_math(text)
    text = _fix_linebreak_before_ampersand(text)
    text = _fix_linebreak_before_hash(text)
    text = _close_unmatched_math_envs(text)
    text = _strip_blank_lines_in_math_envs(text)
    text = _replace_nonascii_lstlisting(text)
    text = _sanitize_lstlisting_language(text)
    text = _strip_linebreaks_at_line_start(text)
    text = _strip_linebreaks_after_item_label(text)
    text = _fix_escaped_quotes(text)
    text = _replace_ascii_quotes(text)
    text = _fix_tabular_alignment(text)
    text = _fix_htmlish_end_tags(text)
    text = _collapse_repeated_linebreaks(text)
    text = _convert_long_inline_code(text)
    text = _isolate_prompt_blocks(text)
    if text != original:
        issues.append("sanitized_content")
    return text, issues


def _sanitize_final_document_math(text: str) -> str:
    begin_tag = r"\begin{document}"
    end_tag = r"\end{document}"
    begin_idx = text.find(begin_tag)
    end_idx = text.rfind(end_tag)
    if begin_idx == -1 or end_idx == -1 or end_idx < begin_idx:
        cleaned, _ = _sanitize_section_tex(text)
        return cleaned

    body_start = begin_idx + len(begin_tag)
    body = text[body_start:end_idx]
    cleaned_body, _ = _sanitize_section_tex(body)
    return text[:body_start] + cleaned_body + text[end_idx:]


def _decode_text_bytes(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1250")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def _preprocess_section_files(g: nx.DiGraph) -> None:
    for node_key in leaf_nodes_in_order(g):
        path = g.nodes[node_key].get("content_file_path")
        if not path:
            continue
        p = Path(path)
        if p.suffix.lower() != ".tex":
            continue
        try:
            raw = p.read_bytes()
        except FileNotFoundError:
            continue
        text = _decode_text_bytes(raw)
        cleaned, issues = _sanitize_section_tex(text)
        # Always normalize to UTF-8, even if no sanitization changed content.
        p.write_text(cleaned, encoding="utf-8")


def _convert_markdown_to_latex(markdown_text: str) -> str:
    import subprocess
    import shutil

    if not shutil.which("pandoc"):
        raise RuntimeError(
            "Pandoc is required to convert Markdown to LaTeX. Install pandoc or use legacy LaTeX mode."
        )
    markdown_text = _prepare_markdown_for_latex(markdown_text)
    # Allow raw LaTeX (e.g., \cite{...}) to pass through from Markdown and
    # recognize TeX math written with both `$...$` and `\(...\)` / `\[...\]`.
    from_candidates = [
        "markdown+raw_tex+tex_math_dollars+tex_math_single_backslash",
        "markdown+raw_tex",
    ]
    last_err = ""
    for from_fmt in from_candidates:
        proc = subprocess.run(
            ["pandoc", f"--from={from_fmt}", "--to=latex", "--wrap=none"],
            input=markdown_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
        last_err = (proc.stderr or proc.stdout or "").strip()
    raise RuntimeError(
        "Pandoc failed to convert Markdown to LaTeX "
        f"(tried {', '.join(from_candidates)}). {last_err}"
    )


def build_markdown_document(g: nx.DiGraph, out_dir: Path) -> Path:
    """
    Assemble a Markdown document from the graph and generated section files.
    Returns path to generated .md file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_heading_text(text: str) -> str:
        return text.replace("\r", " ").replace("\n", " ").strip()

    title = g.nodes["book"].get("title", "Book")
    author = str(g.graph.get("author", "") or "").strip()
    lines: List[str] = [f"# {_sanitize_heading_text(title)}", ""]
    if author:
        lines.append(f"**Author:** {_sanitize_heading_text(author)}")
        lines.append("")

    def add_node(node_key: str) -> None:
        depth = get_depth(node_key)
        node = g.nodes[node_key]
        heading_level = min(6, depth + 1)
        heading = "#" * heading_level
        node_title = _sanitize_heading_text(node.get("title", ""))
        if node_title:
            lines.append(f"{heading} {node_title}")
            lines.append("")

        children = _node_children_sorted(g, node_key)
        summary = str(node.get("summary", "") or "").strip()
        if summary and children:
            lines.append(summary)
            lines.append("")

        if children:
            for ch in children:
                add_node(ch)
            return

        path = node.get("content_file_path")
        if path:
            p = Path(path)
            try:
                raw = p.read_bytes()
            except FileNotFoundError:
                return
            body = _decode_text_bytes(raw).strip()
            if body:
                lines.append(body)
                lines.append("")

    for ch in _node_children_sorted(g, "book"):
        add_node(ch)

    md_filename = safe_filename(title) + ".md"
    md_path = out_dir / md_filename
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return md_path


def export_markdown(tex_path: Path) -> Path:
    """
    Convert LaTeX to Markdown via latex2markdown and post-process.
    """
    import latex2markdown  # type: ignore

    latex_string = tex_path.read_text(encoding="utf-8", errors="ignore")
    l2m = latex2markdown.LaTeX2Markdown(latex_string)
    md = l2m.to_markdown()

    md = re.sub(
        r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}",
        r"![figure](\1)",
        md,
    )

    md = convert_lstlisting_to_markdown(md)
    md = clean_markdown_content(md)

    md_path = tex_path.with_suffix(".md")
    md_path.write_text(md, encoding="utf-8")
    return md_path


