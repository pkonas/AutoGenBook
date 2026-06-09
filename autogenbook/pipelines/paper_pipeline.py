from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx
from pylatex import Command, Document, Package, Section, Subsection, Subsubsection
from pylatex.section import Paragraph, Subparagraph
from pylatex.utils import NoEscape, escape_latex

from autogenbook.graph.doc_graph import attach_content_path, leaf_nodes_in_order, load_graph_json, save_graph_json, sort_node_keys
from autogenbook.agents.base import AgentContext
from autogenbook.agents import StructureSubdividerAgent
from autogenbook.agents.literature_agent import WebLiteratureAgent
from autogenbook.agents.paper_writer import PaperSectionWriterAgent
from autogenbook.citations.extract import extract_citations
from autogenbook.citations.ledger import CitationLedger
from autogenbook.citations.kb_footnotes import apply_kb_footnotes
from autogenbook.audit.latex_auditor import AuditorConfig, audit_latex
from autogenbook.audit.types import AuditSeverity
from autogenbook.llm_usage import log_usage, write_run_meta
from autogenbook.length_control import enforce_section_length
from autogenbook.prompts.agent_prompts import render
from autogenbook.prompts.paper_loader import load_paper_prompts
from autogenbook.prompts.registry import get_prompt, set_prompt_registry
from autogenbook.retrieval.manager import RetrievalManager
from autogenbook.retrieval.mcp_papers import MCPPaperRetriever
from autogenbook.retrieval.tavily import TavilyRetriever
from autogenbook.retrieval.types import RetrievalItem
from openrouter_llm import OpenRouterLLM
from rag_kb import KnowledgeBase
from utils import (
    build_retrieval_query_from_tex,
    extract_first_json_object,
    extract_markdown_fence,
    extract_tex_fence,
    generate_outline_text,
    get_depth,
    safe_filename,
    ensure_robustness_preamble,
)

from ..state import RunContext


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


def _ask_choice(prompt: str, choices: Dict[str, str], default: str) -> str:
    keys = "/".join([k.upper() for k in choices.keys()])
    while True:
        ans = input(f"{prompt} [{keys}] (default {default.upper()}): ").strip().lower()
        if not ans:
            ans = default.lower()
        if ans in choices:
            return ans


def _ask_text(prompt: str, default: Optional[str] = None, required: bool = False) -> str:
    suffix = f" (default {default})" if default else ""
    while True:
        ans = input(f"{prompt}{suffix}: ").strip()
        if ans:
            return ans
        if default:
            return default
        if not required:
            return ""
        print("Zadejte prosim hodnotu.")


def _build_paper_json(
    llm: OpenRouterLLM,
    txt_spec: str,
    kb: Optional[KnowledgeBase],
    citation_style: str,
    target_venue: str,
) -> Dict[str, Any]:
    kb_context = ""
    if kb is not None:
        kb_context = kb.format_context(txt_spec, k=6, max_chars_total=6000)
    prompt_template = get_prompt("paper_json_from_txt_user")
    prompt = render(prompt_template, txt_spec=txt_spec, kb_context=kb_context)
    content = llm.chat(
        [
            {"role": "system", "content": get_prompt("paper_json_from_txt_system")},
            {"role": "user", "content": prompt},
        ],
        allow_tools=False,
    )
    usage = llm.get_last_usage()
    if usage:
        print(llm.format_usage_line(usage, label="paper_json"))
    raw = extract_first_json_object(content)
    raw.setdefault("citation_style", citation_style)
    raw.setdefault("target_venue", target_venue)
    raw.setdefault("max_depth", 3)
    raw.setdefault("max_output_pages", 1.5)
    return raw


def _build_graph_from_paper_json(paper_json: Dict[str, Any]) -> nx.DiGraph:
    g = nx.DiGraph()
    g.graph.update(
        {
            "title": paper_json.get("title", ""),
            "abstract": paper_json.get("abstract", ""),
            "keywords": paper_json.get("keywords", []),
            "target_venue": paper_json.get("target_venue", ""),
            "contributions": paper_json.get("contributions", []),
            "citation_style": paper_json.get("citation_style", "bibtex"),
            "max_depth": int(paper_json.get("max_depth", 3)),
            "max_output_pages": float(paper_json.get("max_output_pages", 1.5)),
        }
    )
    g.add_node(
        "paper",
        title=paper_json.get("title", ""),
        summary=paper_json.get("abstract", ""),
        n_pages=paper_json.get("n_pages", 0),
        needsSubdivision=True,
    )
    for i, sec in enumerate(paper_json.get("sections", []), start=1):
        node = str(i)
        g.add_node(
            node,
            title=str(sec.get("title", "")).strip(),
            summary=str(sec.get("summary", "")).strip(),
            n_pages=float(sec.get("n_pages", 1.0)),
            needsSubdivision=bool(sec.get("needsSubdivision", True)),
        )
        g.add_edge("paper", node)
    return g


def _node_children_sorted(g: nx.DiGraph, node: str) -> List[str]:
    return sort_node_keys(list(g.successors(node)))


def _subdivide_paper_graph(
    llm: OpenRouterLLM,
    g: nx.DiGraph,
    kb: Optional[KnowledgeBase],
    retrieval_manager: Optional[RetrievalManager] = None,
    progress_path: Optional[Path] = None,
    fail_fast_schema: bool = False,
) -> None:
    max_depth = int(g.graph.get("max_depth", 3))
    max_output_pages = float(g.graph.get("max_output_pages", 1.5))
    paper_title = g.graph.get("title", "")
    paper_abstract = g.graph.get("abstract", "")
    target_venue = g.graph.get("target_venue", "")
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
        mode="paper",
        retrieval_manager=retrieval_manager,
        fail_fast_schema=fail_fast_schema,
    )

    frontier = ["paper"]
    for depth in range(1, max_depth + 1):
        next_frontier: List[str] = []
        for parent in frontier:
            for child in _node_children_sorted(g, parent):
                node = g.nodes[child]
                needs_sub = bool(node.get("needsSubdivision", False))
                n_pages = float(node.get("n_pages", 1.0))
                should_subdivide = (needs_sub or n_pages >= max_output_pages) and depth < max_depth
                if not should_subdivide:
                    continue

                query = f"{paper_title}\n{paper_abstract}\n{node.get('title','')}\n{node.get('summary','')}"
                retrieved_context = ""
                if retrieval_manager is not None:
                    items = retrieval_manager.retrieve(query, k=6, allow_web=False)
                    retrieved_context = retrieval_manager.format_context(items, max_chars_total=6000)
                if not retrieved_context:
                    retrieved_context = "(none)"

                max_attempts = 3
                last_err: Optional[Exception] = None
                section_list = None
                for attempt in range(1, max_attempts + 1):
                    try:
                        section_list = subdivider.run(
                            {
                                "doc_kind": "paper",
                                "doc_title": paper_title,
                                "doc_summary": paper_abstract,
                                "target_audience": target_venue,
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


def _related_work_items(related_work: Dict[str, Any]) -> List[RetrievalItem]:
    items: List[RetrievalItem] = []
    for paper in related_work.get("related_papers", []) if isinstance(related_work, dict) else []:
        bibtex = str(paper.get("bibtex", "")).strip()
        if not bibtex:
            continue
        key = paper.get("bib_key")
        if not key:
            match = re.search(r"@\w+\{([^,]+),", bibtex)
            key = match.group(1) if match else None
        if not key:
            key = f"rel_{hashlib.sha1(bibtex.encode('utf-8')).hexdigest()[:10]}"
        title = str(paper.get("title", "")) or "Untitled"
        authors = paper.get("authors") or []
        year = paper.get("year")
        venue = paper.get("venue")
        url = paper.get("url")
        rid = f"RID:web:related:{key}"
        items.append(
            RetrievalItem(
                rid=rid,
                kind="web",
                source="Related Work",
                loc=str(venue) if venue else "",
                score=1.0,
                cite_key=str(key),
                text=str(paper.get("key_points") or paper.get("relevance") or ""),
                url=str(url) if url else None,
                title=title,
                authors=authors,
                year=year,
                venue=str(venue) if venue else None,
                doi=paper.get("doi"),
            )
        )
    return items


def _dedupe_items(items: List[RetrievalItem]) -> List[RetrievalItem]:
    seen = set()
    deduped: List[RetrievalItem] = []
    for item in items:
        key = item.cite_key or item.rid
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _item_keys(items: List[RetrievalItem]) -> set[str]:
    keys: set[str] = set()
    for item in items:
        key = item.cite_key or item.rid
        if key:
            keys.add(str(key))
    return keys


def _ensure_related_work_section(paper_json: Dict[str, Any], related_work: Dict[str, Any]) -> None:
    sections = paper_json.get("sections", [])
    if not isinstance(sections, list):
        sections = []
        paper_json["sections"] = sections
    titles = [str(s.get("title", "")).lower() for s in sections if isinstance(s, dict)]
    if any("related work" in t or "related" == t for t in titles):
        return
    summary = related_work.get("positioning_statement", "")
    if not summary:
        summary = "Related work overview based on retrieved papers."
    sections.insert(
        1 if sections else 0,
        {
            "title": "Related Work",
            "summary": summary,
            "n_pages": 1.0,
            "needsSubdivision": False,
        },
    )


def _is_related_work(title: str) -> bool:
    lowered = title.lower()
    return "related work" in lowered



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


def run_paper(args: Any, run_ctx: Optional[RunContext], logger: Any) -> int:
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

        use_legacy_tex = bool(getattr(args, "legacy_tex", False))
        content_format = "latex" if use_legacy_tex else "markdown"
        prompts = load_paper_prompts(content_format=content_format)
        set_prompt_registry("paper", prompts)
        if run_ctx is None:
            run_ctx = RunContext.create(out_dir=out_dir, mode="paper")
        progress_path = run_ctx.structure_graph_path

        if not input_path.exists():
            print(f"Chyba: vstupnÆð soubor neexistuje: {input_path}", file=sys.stderr)
            return 2

        out_dir.mkdir(parents=True, exist_ok=True)
        json_path.parent.mkdir(parents=True, exist_ok=True)

        kb = None
        if args.kb_dir:
            kb_dir = Path(args.kb_dir).expanduser().resolve()
            print(f"[KB] Buduji/naŽ›ÆðtÆóm znalostnÆð databÆózi z: {kb_dir}")
            kb = KnowledgeBase.build_from_directory(
                kb_dir,
                cache_dir=out_dir / ".kb_cache",
                force_rebuild=bool(args.rebuild_kb),
            )
            print(f"[KB] Hotovo. PoŽ›et chunk‘½: {len(kb.chunks)}")
        else:
            print("[KB] --kb-dir nebyl zadÆón. RAG bude vypnut.")

        tavily = None
        mcp_papers = None
        if getattr(args, "enable_web_rag", False):
            mcp_papers = MCPPaperRetriever()
            api_key = os.environ.get("TAVILY_API_KEY")
            tavily = TavilyRetriever(api_key=api_key)
        retrieval_manager = RetrievalManager(
            local_kb=kb,
            mcp_papers=mcp_papers,
            tavily=tavily,
            enable_web=bool(getattr(args, "enable_web_rag", False)),
            default_k=int(getattr(args, "web_rag_k", 5)),
        )
        if getattr(args, "enable_web_rag", False):
            has_mcp = bool(mcp_papers and mcp_papers.is_available())
            has_tavily = bool(tavily and tavily.api_key)
            if not has_mcp and not has_tavily:
                print(
                    "[WARN] Web RAG enabled but no MCP paper tools or Tavily API key available; using KB only."
                )
            elif not has_mcp and has_tavily:
                print("[INFO] MCP paper tools unavailable; falling back to Tavily search.")

        llm = OpenRouterLLM()
        agent_ctx = AgentContext(
            run_id=run_ctx.run_id,
            out_dir=out_dir,
            kb=kb,
            retrieval_manager=retrieval_manager,
            logger=logger,
            llm=llm,
            mode="paper",
            fail_fast_schema=bool(getattr(args, "fail_fast_schema", False)),
        )
        related_work: Dict[str, Any] = {}
        literature_agent = WebLiteratureAgent(llm)

        g = None
        if args.resume and progress_path.exists():
            print(f"[RESUME] NaŽ›ÆðtÆóm ulo‘–enou strukturu z: {progress_path}")
            g = load_graph_json(progress_path)
        else:
            t0 = time.perf_counter()
            paper_json = None
            if json_path.exists():
                if args.use_txt:
                    choice = "t"
                elif args.use_json:
                    choice = "j"
                else:
                    choice = _ask_choice(
                        f"JSON soubor u‘– existuje ({json_path.name}). Pou‘–Æðt existujÆðcÆð JSON, nebo znovu vygenerovat z TXT?",
                        {"j": "json", "t": "txt"},
                        default="j",
                    )
                if choice == "j":
                    paper_json = json.loads(_read_text(json_path))
                else:
                    txt_spec = _read_text(input_path)
                    paper_json = _build_paper_json(
                        llm,
                        txt_spec,
                        kb=kb,
                        citation_style=args.citation_style,
                        target_venue=args.paper_venue,
                    )
                    usage = llm.get_last_usage()
                    if usage:
                        log_usage(out_dir, "paper_json", llm, usage, {"stage": "paper_json"})
                    json_path.write_text(json.dumps(paper_json, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                txt_spec = _read_text(input_path)
                paper_json = _build_paper_json(
                    llm,
                    txt_spec,
                    kb=kb,
                    citation_style=args.citation_style,
                    target_venue=args.paper_venue,
                )
                usage = llm.get_last_usage()
                if usage:
                    log_usage(out_dir, "paper_json", llm, usage, {"stage": "paper_json"})
                json_path.write_text(json.dumps(paper_json, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[JSON] Trvani: {_format_duration(time.perf_counter() - t0)}")

            related_work = literature_agent.run(
                {"idea": paper_json.get("title", ""), "keywords": paper_json.get("keywords", [])},
                agent_ctx,
            )
            (out_dir / "related_work.json").write_text(
                json.dumps(related_work, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _ensure_related_work_section(paper_json, related_work)
            json_path.write_text(json.dumps(paper_json, ensure_ascii=False, indent=2), encoding="utf-8")

            g = _build_graph_from_paper_json(paper_json)
            _subdivide_paper_graph(
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
            author = _ask_text("Zadejte autora clanku", required=True)
            g.graph["author"] = author
            save_graph_json(g, progress_path)

        citation_style = str(getattr(args, "citation_style", "") or g.graph.get("citation_style", "bibtex"))
        g.graph["citation_style"] = citation_style
        paper_title = g.graph.get("title", "")
        paper_abstract = g.graph.get("abstract", "")
        paper_keywords = g.graph.get("keywords", [])
        target_venue = g.graph.get("target_venue", "")
        contributions = g.graph.get("contributions", [])

        if not related_work and (out_dir / "related_work.json").exists():
            related_work = json.loads(_read_text(out_dir / "related_work.json"))

        toc_and_summary = generate_outline_text(
            {
                n: {
                    "title": g.nodes[n].get("title", ""),
                    "summary": g.nodes[n].get("summary", ""),
                    "n_pages": g.nodes[n].get("n_pages", ""),
                    "children": _node_children_sorted(g, n),
                }
                for n in g.nodes
            },
            root="paper",
        )

        sections_dir = out_dir / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)
        section_ext = ".md" if content_format == "markdown" else ".tex"

        def _normalize_section_output(text: str) -> str:
            if content_format == "markdown":
                return extract_markdown_fence(text)
            return extract_tex_fence(text)

        writer = PaperSectionWriterAgent(llm)
        citation_items: Dict[str, RetrievalItem] = {}
        rid_items: Dict[str, RetrievalItem] = {}
        related_items = _related_work_items(related_work)
        for item in related_items:
            if item.cite_key and item.cite_key not in citation_items:
                citation_items[item.cite_key] = item
            if item.rid and item.rid not in rid_items:
                rid_items[item.rid] = item

        for node_key in leaf_nodes_in_order(g):
            node = g.nodes[node_key]
            if args.resume:
                existing_path = node.get("content_file_path") or ""
                if existing_path and Path(existing_path).suffix.lower() != section_ext:
                    existing_path = ""
                    node["content_file_path"] = ""
                if not existing_path:
                    existing_path = str((sections_dir / f"{node_key}{section_ext}").resolve())
                    node["content_file_path"] = existing_path
                if existing_path and Path(existing_path).exists():
                    continue

            query = f"{paper_title}\n{paper_abstract}\n{node.get('title','')}\n{node.get('summary','')}"
            initial_items = retrieval_manager.retrieve(
                query, k=retrieval_manager.default_k, allow_web=False
            )
            context_items = _dedupe_items(initial_items + related_items)
            retrieved_context = retrieval_manager.format_context(context_items)

            paper_outline_text = toc_and_summary
            figures_manifest = "[]"
            tables_manifest = "[]"

            prompt_input = {
                "paper_title": paper_title,
                "paper_venue": target_venue,
                "paper_abstract": paper_abstract,
                "paper_keywords": paper_keywords,
                "paper_outline_text": paper_outline_text,
                "node_key": node_key,
                "section_title": node.get("title", ""),
                "section_summary": node.get("summary", ""),
                "n_pages": node.get("n_pages", 1.0),
                "plan_json": "{}",
                "literature_json": json.dumps(related_work, ensure_ascii=False, indent=2),
                "analysis_json": "{}",
                "figures_manifest": figures_manifest,
                "tables_manifest": tables_manifest,
                "retrieved_context": retrieved_context or "(none)",
                "section_draft": "",
            }
            tex = writer.run(prompt_input, agent_ctx)
            tex = _normalize_section_output(tex)
            if retrieval_manager.enable_web:
                draft_query = build_retrieval_query_from_tex(tex, max_chars=1200)
                if draft_query:
                    refined_query = f"{node.get('title','')}\n{draft_query}"
                    refined_items = retrieval_manager.retrieve(
                        refined_query, k=retrieval_manager.default_k, allow_web=True
                    )
                    merged_items = _dedupe_items(context_items + refined_items)
                    if _item_keys(merged_items) != _item_keys(context_items):
                        context_items = merged_items
                        prompt_input["retrieved_context"] = (
                            retrieval_manager.format_context(context_items) or "(none)"
                        )
                        prompt_input["section_draft"] = tex
                        tex = writer.run(prompt_input, agent_ctx)
                        tex = _normalize_section_output(tex)

            for item in context_items:
                if item.cite_key and item.cite_key not in citation_items:
                    citation_items[item.cite_key] = item
                if item.rid and item.rid not in rid_items:
                    rid_items[item.rid] = item
            tex, _ = enforce_section_length(
                llm,
                tex,
                target_pages=float(node.get("n_pages", 1.0)),
                out_dir=out_dir,
                label="paper_section",
            )
            if content_format == "latex":
                from book_builder import _sanitize_section_tex
                tex, _ = _sanitize_section_tex(tex)
            else:
                tex = tex.strip()

            section_path = sections_dir / f"{node_key}{section_ext}"
            section_path.write_text(tex, encoding="utf-8")
            attach_content_path(g, node_key, section_path)
            if progress_path is not None:
                save_graph_json(g, progress_path)

        outputs = []
        md_path: Optional[Path] = None
        if content_format == "markdown" and not args.no_md:
            md_path = build_paper_markdown_document(g, out_dir)
            outputs.append(md_path)

        tex_path = _build_paper_latex(g, out_dir, citation_style=citation_style)
        if not args.no_tex:
            outputs.append(tex_path)

        latex = tex_path.read_text(encoding="utf-8", errors="ignore")
        if citation_style == "bibtex":
            cite_keys, rids = extract_citations(latex)
            ledger = CitationLedger()
            missing = []
            for key in sorted(cite_keys):
                item = citation_items.get(key)
                if not item:
                    missing.append(key)
                    continue
                ledger.add_from_retrieval_item(item)
            for rid in sorted(rids):
                item = rid_items.get(rid)
                if not item:
                    continue
                ledger.add_from_retrieval_item(item)

            if missing:
                missing_msg = ", ".join(missing)
                print(
                    "[WARN] Missing citations for keys: "
                    + missing_msg
                    + ". Writing placeholder BibTeX entries."
                )
                for key in missing:
                    ledger.add_placeholder(key)
            ledger.write_bib(out_dir / "refs.bib")

        kb_cite_map = {
            key: (item.source, item.loc)
            for key, item in citation_items.items()
            if item.kind == "kb"
        }
        kb_rid_map = {
            rid: (item.source, item.loc)
            for rid, item in rid_items.items()
            if item.kind == "kb"
        }
        if citation_style == "footnote":
            latex = apply_kb_footnotes(latex, kb_cite_map, kb_rid_map)
        if citation_style == "bibtex" and "\\bibliography" not in latex and (out_dir / "refs.bib").exists():
            latex = latex.replace(
                "\\end{document}",
                "\n\\bibliographystyle{plain}\n\\bibliography{refs}\n\\end{document}",
            )
        tex_path.write_text(latex, encoding="utf-8")

        audit_enabled = getattr(args, "audit", None)
        if audit_enabled is None:
            audit_enabled = True
        audit_mode = str(getattr(args, "audit_mode", "warn") or "warn")
        audit_window = int(getattr(args, "audit_window_chars", 600) or 600)
        if audit_mode == "off":
            audit_enabled = False
        if audit_enabled:
            tex_text = tex_path.read_text(encoding="utf-8", errors="ignore")
            known_cite_keys = set(citation_items.keys())
            known_rids = set(rid_items.keys())
            report = audit_latex(
                tex_path=tex_path,
                tex_text=tex_text,
                doc_kind="paper",
                known_cite_keys=known_cite_keys,
                known_rids=known_rids,
                project_root=Path.cwd(),
                config=AuditorConfig(
                    enabled=True,
                    mode=audit_mode,
                    evidence_window_chars=audit_window,
                ),
            )
            report.dump(out_dir / "audit_report.json")
            if audit_mode == "strict" and report.counts_by_severity.get(AuditSeverity.ERROR.value, 0) > 0:
                status = "error"
                error = "Audit failed in strict mode. See audit_report.json for details."
                return 4

        if not args.no_pdf:
            from book_builder import compile_pdf

            pdf_path = compile_pdf(tex_path)
            outputs.append(pdf_path)
        if content_format == "latex" and not args.no_md:
            from book_builder import export_markdown

            md_path = export_markdown(tex_path)
            outputs.append(md_path)

        print("\nHotovo. VÆñstupy:")
        for p in outputs:
            print(f" - {p}")
        print(f"[TOKENS] Celkem spotrebovano tokenu: {llm.get_total_tokens()}")
        total_cost = llm.get_total_cost_usd()
        if total_cost is not None:
            print(f"[COST] Celkova cena: ${total_cost:.6f}")
        return 0
    except Exception as exc:
        status = "error"
        error = str(exc)
        raise
    finally:
        finished_at = datetime.now(timezone.utc)
        if run_ctx is not None:
            _write_run_meta(run_ctx, args, started_at, finished_at, status, error, llm)


def build_paper_markdown_document(g: nx.DiGraph, out_dir: Path) -> Path:
    """
    Assemble a Markdown document from the graph and generated section files.
    Returns path to generated .md file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_heading_text(text: str) -> str:
        return text.replace("\r", " ").replace("\n", " ").strip()

    title = g.graph.get("title", "Paper")
    author = str(g.graph.get("author", "") or "").strip()
    lines: List[str] = [f"# {_sanitize_heading_text(title)}", ""]
    if author:
        lines.append(f"**Author:** {_sanitize_heading_text(author)}")
        lines.append("")

    abstract = str(g.graph.get("abstract", "") or "").strip()
    if abstract:
        lines.append("## Abstract")
        lines.append("")
        lines.append(abstract)
        lines.append("")

    keywords = g.graph.get("keywords", [])
    if keywords:
        kw = ", ".join(keywords) if isinstance(keywords, list) else str(keywords)
        lines.append(f"**Keywords:** {_sanitize_heading_text(kw)}")
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
        if children:
            for ch in children:
                add_node(ch)
            return

        path = node.get("content_file_path")
        if path:
            body = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
            if body:
                lines.append(body)
                lines.append("")

    for ch in _node_children_sorted(g, "paper"):
        add_node(ch)

    md_filename = safe_filename(title) + ".md"
    md_path = out_dir / md_filename
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return md_path


def _build_paper_latex(g: nx.DiGraph, out_dir: Path, citation_style: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    geometry_options = {"a4paper": True, "margin": "25mm"}
    doc = Document(documentclass="article", geometry_options=geometry_options)
    doc.packages.append(Package("amsmath"))
    doc.packages.append(Package("amssymb"))
    doc.packages.append(Package("amsfonts"))
    doc.packages.append(Package("mathtools"))
    doc.packages.append(Package("bm"))
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
    doc.packages.append(Package("booktabs"))
    doc.packages.append(Package("underscore", options="strings"))
    doc.packages.append(Package("url"))
    doc.packages.append(Package("xurl"))
    doc.packages.append(Package("hyperref"))
    doc.packages.append(Package("bookmark"))
    doc.packages.append(Package("natbib"))
    doc.preamble.append(NoEscape(r"\graphicspath{{figures/}}"))
    doc.preamble.append(NoEscape(r"\providecommand{\tightlist}{}"))
    ensure_robustness_preamble(out_dir)
    doc.preamble.append(NoEscape(r"\input{robustness.tex}"))

    def _sanitize_heading_text(text: str) -> NoEscape:
        cleaned = text.replace("\r", " ").replace("\n", " ").strip()
        return NoEscape(escape_latex(cleaned))

    title = g.graph.get("title", "Paper")
    doc.preamble.append(Command("title", _sanitize_heading_text(title)))
    author = str(g.graph.get("author", "") or "").strip() or "AutoGenBook"
    doc.preamble.append(Command("author", _sanitize_heading_text(author)))
    doc.preamble.append(Command("date", NoEscape(r"\today")))
    doc.append(NoEscape(r"\hypersetup{pageanchor=false}"))
    doc.append(NoEscape(r"\maketitle"))
    doc.append(NoEscape(r"\pagenumbering{arabic}"))
    doc.append(NoEscape(r"\hypersetup{pageanchor=true}"))

    abstract = g.graph.get("abstract", "")
    if abstract:
        doc.append(NoEscape(r"\begin{abstract}"))
        doc.append(NoEscape(escape_latex(abstract)))
        doc.append(NoEscape(r"\end{abstract}"))

    keywords = g.graph.get("keywords", [])
    if keywords:
        kw = ", ".join(keywords) if isinstance(keywords, list) else str(keywords)
        doc.append(NoEscape(r"\noindent\textbf{Keywords:} " + escape_latex(kw)))

    def heading_class_for_depth(d: int):
        if d == 1:
            return Section
        if d == 2:
            return Subsection
        if d == 3:
            return Subsubsection
        if d == 4:
            return Paragraph
        return Subparagraph

    def add_node(parent_container, node_key: str) -> None:
        depth = get_depth(node_key)
        node = g.nodes[node_key]
        title = node.get("title", "")
        Heading = heading_class_for_depth(depth)

        with parent_container.create(Heading(_sanitize_heading_text(title), label=False)):
            children = _node_children_sorted(g, node_key)
            if children:
                for ch in children:
                    add_node(parent_container, ch)
            else:
                path = node.get("content_file_path")
                if path:
                    from book_builder import _sanitize_section_tex, _convert_markdown_to_latex

                    p = Path(path)
                    tex = p.read_text(encoding="utf-8", errors="ignore")
                    if p.suffix.lower() == ".md":
                        tex = _convert_markdown_to_latex(tex)
                    tex, _ = _sanitize_section_tex(tex)
                    parent_container.append(NoEscape("\n\n"))
                    parent_container.append(NoEscape(tex))
                    parent_container.append(NoEscape("\n\n"))

    for ch in _node_children_sorted(g, "paper"):
        add_node(doc, ch)

    if citation_style == "bibtex":
        doc.append(NoEscape(r"\bibliographystyle{plain}"))
        doc.append(NoEscape(r"\bibliography{refs}"))

    tex_filename = safe_filename(title) + ".tex"
    tex_path = out_dir / tex_filename
    doc.generate_tex(filepath=str(tex_path.with_suffix("")))
    return tex_path
